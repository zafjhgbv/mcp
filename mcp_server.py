"""
A minimal and complete framework for an MCP (Model Context Protocol) server.

This server provides multiple services including:
1. A simple "Greeter" service with a single tool: `hello`
2. A web content fetching service with tool: `fetch_web_content`
3. A deep research service with tool: `deep_research`
4. Jira-Confluence-Dify sync services with tools:
   - `sync_all_to_dify`: 全量同步 Jira + Confluence
   - `sync_jira_to_dify`: 仅同步 Jira Issues
   - `sync_confluence_to_dify`: 仅同步 Confluence Pages
   - `query_sync_records`: 查询同步历史记录

It is built using the official `mcp` Python SDK and follows the patterns
from the official documentation.

This version is modified to use the SSE transport over HTTP, allowing it to be
load-balanced.

To run this server:
1. Make sure you have the dependencies installed: pip install -r requirements.txt
2. Execute the script with host and port: python mcp_server.py --port 8001
"""
import argparse
import uvicorn
import asyncio
import json
import requests
from bs4 import BeautifulSoup
import re
import os
import sys
import concurrent.futures
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import Union, List, Dict, Optional, Any
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount

# 加载环境变量
try:
    from dotenv import load_dotenv
    # 加载当前目录的 .env 文件
    load_dotenv()
    print("Environment variables loaded from .env file")
except ImportError:
    print("python-dotenv not installed, using system environment variables only")

# 导入同步模块
import logging
try:
    from sync_modules import (
        setup_database,
        sync_all_sources,
        sync_jira_only,
        sync_confluence_only,
        query_sync_history
    )
    SYNC_MODULES_AVAILABLE = True
    print("Jira-Confluence-Dify sync modules loaded successfully")
    
    # 配置 logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("sync.log", encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # 初始化数据库
    try:
        if setup_database():
            print("Sync database initialized successfully")
        else:
            print("Warning: Sync database initialization failed")
    except Exception as e:
        print(f"Warning: Sync database initialization error: {e}")
        SYNC_MODULES_AVAILABLE = False
        
except ImportError as e:
    SYNC_MODULES_AVAILABLE = False
    print(f"Warning: Sync modules not available: {e}")
    print("Jira-Confluence-Dify sync tools will not be registered")

# DuckDuckGo搜索库导入
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None
        print("Warning: DuckDuckGo search library not found. Deep research functionality will be limited.")


class DirectWebFetcher:
    """网页内容抓取器类，集成到MCP服务器中"""
    
    def __init__(self, proxy_port: Optional[int] = None):
        self.proxy_port = proxy_port
        self.session = requests.Session()
        
        # 设置请求头，模拟真实浏览器
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        
        # 代理配置
        if proxy_port:
            self.session.proxies = {
                'http': f'http://127.0.0.1:{proxy_port}',
                'https': f'http://127.0.0.1:{proxy_port}'
            }
    
    def remove_ads_and_noise(self, soup):
        """移除广告和无关元素"""
        selectors_to_remove = [
            'script', 'style', 'iframe', 'ins', '.ads', '[class*="ads"]',
            '[id*="ads"]', '.advertisement', '[class*="advertisement"]',
            '[id*="advertisement"]', '.banner', '[class*="banner"]', '[id*="banner"]',
            '.popup', '[class*="popup"]', '[id*="popup"]', 'nav', 'aside', 'footer',
            '[aria-hidden="true"]'
        ]
        
        for selector in selectors_to_remove:
            try:
                elements = soup.select(selector)
                for element in elements:
                    element.decompose()
            except Exception:
                continue
    
    def extract_main_content(self, soup):
        """智能提取正文内容"""
        # 1. 精准提取正文
        content_selectors = [
            '[data-testid="article"]', 'article', '.content',
            '.main', '.post-content', 'main'
        ]
        
        main_content = None
        for selector in content_selectors:
            try:
                main_content = soup.select_one(selector)
                if main_content:
                    break
            except Exception:
                continue
        
        # 如果找不到特定正文容器，就退回到body
        if not main_content:
            main_content = soup.find('body') or soup
        
        # 提取文本并清理
        text = main_content.get_text(separator='\n', strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        potential_text = '\n'.join([line.strip() for line in text.split('\n') if line.strip()])
        
        # 2. 全面提取链接 (从整个body)
        all_links = soup.find_all('a', href=True)
        links = []
        
        for link in all_links:
            link_text = link.get_text(strip=True)
            url = link.get('href', '')
            
            # 确保链接有文本、是有效链接
            if link_text and url and (url.startswith('http') or url.startswith('/')):
                # 处理相对链接
                if url.startswith('/'):
                    base_url = soup.find('base')
                    if base_url and base_url.get('href'):
                        url = urljoin(base_url['href'], url)
                    else:
                        # 从当前页面URL推断base URL
                        url = urljoin(self.current_url, url)
                
                links.append({'text': link_text, 'url': url})
        
        # 去重
        unique_links = []
        seen_urls = set()
        for link in links:
            if link['url'] not in seen_urls:
                unique_links.append(link)
                seen_urls.add(link['url'])
        
        # 3. 智能组合输出
        if len(potential_text) > 200:
            # 文章页 (文本长)
            combined_result = potential_text
            if unique_links:
                link_text = '\n'.join([f"- {link['text']}: {link['url']}" for link in unique_links])
                combined_result += '\n\n--- 页面包含的链接 ---\n' + link_text
            return combined_result
        else:
            # 列表页 (文本短)
            if unique_links:
                return unique_links  # 主要返回链接
            return potential_text  # 如果没链接，返回短文本
    
    def fetch_url(self, url: str) -> Union[str, List[Dict[str, str]]]:
        """直接抓取模式的主要函数"""
        self.current_url = url
        
        try:
            # 发送请求
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or 'utf-8'
            
            # 解析HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 移除广告和噪音
            self.remove_ads_and_noise(soup)
            
            # 提取主要内容
            extracted_data = self.extract_main_content(soup)
            
            return extracted_data
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"网络请求失败: {str(e)}")
        except Exception as e:
            raise Exception(f"内容提取失败: {str(e)}")

class FlashDeepSearchPython:
    def __init__(self):
        self.deep_search_model = os.getenv('DeepSearchModel', 'gpt-4o')
        self.search_engine = os.getenv('SearchEngine', 'duckduckgo')
        self.max_search_list = int(os.getenv('MaxSearchList', '8'))
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        self.openai_base_url = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
          
    def call_ai_model(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """调用AI模型"""
        headers = {
            'Authorization': f'Bearer {self.openai_api_key}',
            'Content-Type': 'application/json'
        }
          
        payload = {
            'model': self.deep_search_model,
            'messages': messages,
            'temperature': 0.7,
            'max_tokens': 4000
        }
          
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto'
          
        try:
            response = requests.post(
                f'{self.openai_base_url}/chat/completions',
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise Exception(f"AI模型调用失败: {str(e)}")
      
    def generate_keywords(self, search_content: str, search_broadness: int) -> List[str]:
        """生成搜索关键词"""
        messages = [
            {
                "role": "system",
                "content": f"""你是一个专业的研究助手。根据用户提供的研究主题，生成{search_broadness}个不同维度的搜索关键词。
  
要求：
1. 关键词应该覆盖主题的不同方面和角度
2. 包含中英文关键词，确保搜索的全面性
3. 关键词应该具体且有针对性
4. 避免过于宽泛或重复的关键词
5. 直接返回关键词列表，每行一个，不需要编号或其他格式"""
            },
            {
                "role": "user",
                "content": f"研究主题：{search_content}\n请生成{search_broadness}个搜索关键词："
            }
        ]
          
        try:
            response = self.call_ai_model(messages)
            content = response['choices'][0]['message']['content']
            keywords = [kw.strip() for kw in content.split('\n') if kw.strip()]
            return keywords[:search_broadness]  # 确保不超过指定数量
        except Exception as e:
            # 如果AI调用失败，返回基础关键词
            return [search_content, f"{search_content} 应用", f"{search_content} 发展趋势"]

    def is_valid_url(self, url: str) -> bool:
        """检查是否为有效的URL"""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False
    
    def is_url_match(self, result_url: str, filter_url: str) -> bool:
        """检查搜索结果URL是否匹配筛选URL的域名"""
        try:
            result_parsed = urlparse(result_url)
            filter_parsed = urlparse(filter_url)
            
            result_domain = result_parsed.netloc.lower()
            filter_domain = filter_parsed.netloc.lower()
            
            # 去掉www前缀进行比较
            result_domain = result_domain.replace('www.', '', 1)
            filter_domain = filter_domain.replace('www.', '', 1)
            
            # 检查是否为同一域名或子域名
            return result_domain == filter_domain or result_domain.endswith('.' + filter_domain) or filter_domain.endswith('.' + result_domain)
            
        except Exception:
            return False

    def extract_text_from_html(self, html_content: str, url: str = '') -> str:
        """从HTML内容中提取文本"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 移除脚本和样式元素
            for script in soup(["script", "style"]):
                script.decompose()
            
            # 获取标题
            title = soup.find('title')
            title_text = title.get_text() if title else ''
            
            # 获取主要内容
            # 尝试找到主要内容区域
            main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|article'))
            
            if main_content:
                text = main_content.get_text()
            else:
                text = soup.get_text()
            
            # 清理文本
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            # 限制文本长度
            max_length = 5000
            if len(text) > max_length:
                text = text[:max_length] + "..."
            
            return f"标题: {title_text}\n\n内容: {text}"
            
        except Exception as e:
            return f"HTML解析失败: {str(e)}"

    def fetch_url_content(self, url: str) -> Dict[str, Any]:
        """获取URL内容"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()
            
            content_type = response.headers.get('content-type', '').lower()
            
            if 'text/html' in content_type or 'text/plain' in content_type:
                if 'text/html' in content_type:
                    extracted_text = self.extract_text_from_html(response.text, url)
                else:
                    extracted_text = response.text[:5000]  # 限制纯文本长度
                
                return {
                    'url': url,
                    'title': f"URL内容: {urlparse(url).netloc}",
                    'content': extracted_text,
                    'status': 'success',
                    'content_type': content_type
                }
            else:
                return {
                    'url': url,
                    'title': f"URL内容: {urlparse(url).netloc}",
                    'content': f"不支持的内容类型: {content_type}",
                    'status': 'unsupported_type',
                    'content_type': content_type
                }
                
        except requests.exceptions.RequestException as e:
            return {
                'url': url,
                'title': f"URL内容: {urlparse(url).netloc}",
                'content': f"获取失败: {str(e)}",
                'status': 'error',
                'error': str(e)
            }
        except Exception as e:
            return {
                'url': url,
                'title': f"URL内容: {urlparse(url).netloc}",
                'content': f"处理失败: {str(e)}",
                'status': 'error',
                'error': str(e)
            }

    def search_from_url(self, url: str) -> Dict[str, Any]:
        """从URL获取内容作为搜索结果"""
        url_content = self.fetch_url_content(url)
        
        return {
            'query': f"URL: {url}",
            'results': [{
                'title': url_content['title'],
                'url': url_content['url'],
                'snippet': url_content['content'][:500] + "..." if len(url_content['content']) > 500 else url_content['content']
            }] if url_content['status'] == 'success' else [],
            'total_results': 1 if url_content['status'] == 'success' else 0,
            'url_content': url_content,
            'source_type': 'url'
        }
      
    def search_duckduckgo(self, query: str, filter_urls: List[str] = None) -> Dict[str, Any]:
        """使用DuckDuckGo搜索，可选择按URL域名筛选结果"""
        if DDGS is None:
            return {
                'query': query,
                'error': "DuckDuckGo搜索库未安装，请安装 ddgs 或 duckduckgo-search",
                'results': [],
                'source_type': 'duckduckgo'
            }
            
        try:
            with DDGS() as ddgs:
                # 使用正确的参数名 'query' 而不是 'keywords'
                raw_results = list(ddgs.text(query=query, safesearch="moderate", max_results=20))  # 增加搜索结果数量以便筛选
                
                results = []
                filtered_results = []
                
                for item in raw_results:
                    result_item = {
                        'title': item.get('title', ''),
                        'url': item.get('href', ''),
                        'snippet': item.get('body', '')
                    }
                    results.append(result_item)
                    
                    # 如果指定了URL筛选，则检查是否匹配
                    if filter_urls:
                        result_url = result_item['url']
                        for filter_url in filter_urls:
                            if self.is_url_match(result_url, filter_url):
                                filtered_results.append(result_item)
                                break
                
                # 如果有URL筛选，返回筛选后的结果，否则返回前5个原始结果
                final_results = filtered_results if filter_urls else results[:5]
                
                return {
                    'query': query,
                    'results': final_results,
                    'total_results': len(final_results),
                    'source_type': 'duckduckgo',
                    'filtered': bool(filter_urls),
                    'filter_urls': filter_urls or [],
                    'original_count': len(results),
                    'filtered_count': len(filtered_results) if filter_urls else 0
                }
        except Exception as e:
            return {
                'query': query,
                'error': f"搜索失败: {str(e)}",
                'results': [],
                'source_type': 'duckduckgo'
            }

    def execute_mixed_searches(self, keywords: List[str], urls: List[str] = None, filter_urls: List[str] = None) -> List[Dict]:
        """执行混合搜索（关键词 + URL），支持URL筛选"""
        search_results = []
        all_search_items = []
        
        # 添加关键词搜索任务
        for keyword in keywords[:self.max_search_list]:
            all_search_items.append(('keyword', keyword))
        
        # 添加URL搜索任务
        if urls:
            for url in urls:
                if self.is_valid_url(url):
                    all_search_items.append(('url', url))
        
        # 限制总的搜索任务数量
        max_workers = min(self.max_search_list, len(all_search_items))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有搜索任务
            future_to_item = {}
            for search_type, search_item in all_search_items:
                if search_type == 'keyword':
                    future = executor.submit(self.search_duckduckgo, search_item, filter_urls)
                else:  # url
                    future = executor.submit(self.search_from_url, search_item)
                future_to_item[future] = (search_type, search_item)
            
            # 收集结果
            for future in concurrent.futures.as_completed(future_to_item):
                search_type, search_item = future_to_item[future]
                try:
                    result = future.result()
                    search_results.append(result)
                except Exception as e:
                    search_results.append({
                        'query': f"{search_type}: {search_item}",
                        'error': f"搜索异常: {str(e)}",
                        'results': [],
                        'source_type': search_type
                    })
        
        return search_results
      
    def execute_concurrent_searches(self, keywords: List[str], filter_urls: List[str] = None) -> List[Dict]:
        """并发执行搜索，可选择按URL域名筛选"""
        search_results = []
          
        # 限制并发数量
        max_workers = min(self.max_search_list, len(keywords))
          
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有搜索任务
            future_to_keyword = {
                executor.submit(self.search_duckduckgo, keyword, filter_urls): keyword
                for keyword in keywords[:self.max_search_list]
            }
              
            # 收集结果
            for future in concurrent.futures.as_completed(future_to_keyword):
                keyword = future_to_keyword[future]
                try:
                    result = future.result()
                    search_results.append(result)
                except Exception as e:
                    search_results.append({
                        'query': keyword,
                        'error': f"搜索异常: {str(e)}",
                        'results': [],
                        'source_type': 'duckduckgo'
                    })
          
        return search_results
      
    def generate_report(self, search_content: str, search_results: List[Dict], filter_urls: List[str] = None) -> str:
        """生成研究报告"""
        # 整理搜索数据
        all_results_text = ""
        filtered_info = ""
        
        if filter_urls:
            filtered_domains = [urlparse(url).netloc for url in filter_urls]
            filtered_info = f"\n**注意：本报告的搜索结果已按以下域名筛选：{', '.join(filtered_domains)}**\n"
        
        for search_result in search_results:
            query = search_result.get('query', '')
            source_type = search_result.get('source_type', 'unknown')
            is_filtered = search_result.get('filtered', False)
            
            if 'error' in search_result:
                all_results_text += f"\n「{query}」搜索失败: {search_result['error']}\n"
                continue
                  
            results = search_result.get('results', [])
            if results:
                if source_type == 'url':
                    all_results_text += f"\n=== URL源「{query}」的内容 ===\n"
                elif is_filtered:
                    original_count = search_result.get('original_count', 0)
                    filtered_count = search_result.get('filtered_count', 0)
                    all_results_text += f"\n=== 关键词「{query}」的筛选结果（筛选后 {filtered_count}/{original_count} 条）===\n"
                else:
                    all_results_text += f"\n=== 关键词「{query}」的搜索结果 ===\n"
                    
                for i, result in enumerate(results, 1):
                    title = result.get('title', '无标题')
                    url = result.get('url', '')
                    snippet = result.get('snippet', '无摘要')
                    
                    if source_type == 'url':
                        # URL源显示更多内容
                        all_results_text += f"{i}. {title}\n   来源: {url}\n   内容: {snippet}\n\n"
                    else:
                        # 搜索结果显示摘要
                        domain = urlparse(url).netloc if url else '未知域名'
                        all_results_text += f"{i}. {title}\n   链接: {url}\n   域名: {domain}\n   摘要: {snippet}\n\n"
            elif is_filtered:
                # 如果筛选后没有结果
                original_count = search_result.get('original_count', 0)
                all_results_text += f"\n=== 关键词「{query}」的筛选结果 ===\n筛选后无匹配结果（原始搜索共 {original_count} 条）\n\n"
          
        system_content = """你是一个专业的研究分析师。基于提供的搜索数据和URL内容，撰写一份全面、深入的研究报告。

报告要求：
1. 结构清晰，包含引言、主要发现、详细分析、结论等部分
2. 充分利用搜索到的信息和URL内容，进行深度分析和综合
3. 保持客观中立，基于事实进行分析
4. 适当引用搜索结果和URL内容中的具体信息
5. 区分网络搜索结果和直接URL内容源，明确标注信息来源
6. 如果搜索结果经过URL域名筛选，请在报告中特别说明这一点
7. 报告应该有实用价值，能够为读者提供有价值的洞察
8. 使用Markdown格式，确保可读性"""

        if filter_urls:
            system_content += f"\n\n特别注意：本次搜索结果已按特定URL域名筛选，重点关注来自这些域名的信息：{', '.join([urlparse(url).netloc for url in filter_urls])}"
        
        messages = [
            {
                "role": "system",
                "content": system_content
            },
            {
                "role": "user",
                "content": f"""研究主题：{search_content}
{filtered_info}
基于以下搜索数据和URL内容，请撰写一份深度研究报告：

{all_results_text}

请确保报告内容丰富、分析深入，并适当引用搜索结果和URL内容中的信息。特别注意区分不同来源的信息{f'，并突出经过域名筛选的搜索结果' if filter_urls else ''}。"""
            }
        ]
          
        try:
            response = self.call_ai_model(messages)
            return response['choices'][0]['message']['content']
        except Exception as e:
            return f"报告生成失败: {str(e)}\n\n原始搜索数据：\n{all_results_text}"
      
    def save_report(self, content: str, search_content: str) -> str:
        """保存报告到文件"""
        try:
            # 创建文件目录
            file_dir = "file/document"
            os.makedirs(file_dir, exist_ok=True)
              
            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_title = "".join(c for c in search_content if c.isalnum() or c in (' ', '-', '_')).rstrip()[:50]
            filename = f"深度研究_{safe_title}_{timestamp}.md"
            filepath = os.path.join(file_dir, filename)
              
            # 保存文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
              
            return filepath
        except Exception as e:
            return f"文件保存失败: {str(e)}"
      
    def process_request(self, input_data: Dict) -> Dict:
        """处理搜索请求"""
        try:
            search_content = input_data.get('SearchContent', '').strip()
            if not search_content:
                return {
                    "status": "error",
                    "error": "SearchContent参数不能为空"
                }
              
            search_broadness = int(input_data.get('SearchBroadness', 10))
            if search_broadness < 5 or search_broadness > 20:
                search_broadness = 10
            
            # 获取URL列表（作为内容源）
            search_urls = input_data.get('SearchUrls', [])
            if isinstance(search_urls, str):
                # 如果是字符串，按换行符或逗号分割
                search_urls = [url.strip() for url in search_urls.replace('\n', ',').split(',') if url.strip()]
            elif not isinstance(search_urls, list):
                search_urls = []
            
            # 获取URL筛选列表（用于筛选搜索结果）
            filter_urls = input_data.get('FilterUrls', [])
            if isinstance(filter_urls, str):
                # 如果是字符串，按换行符或逗号分割
                filter_urls = [url.strip() for url in filter_urls.replace('\n', ',').split(',') if url.strip()]
            elif not isinstance(filter_urls, list):
                filter_urls = []
            
            # 验证URL
            valid_urls = []
            for url in search_urls:
                if self.is_valid_url(url):
                    valid_urls.append(url)
                else:
                    print(f"[FlashDeepSearch] 警告: 无效搜索源URL被跳过: {url}")
            
            valid_filter_urls = []
            for url in filter_urls:
                if self.is_valid_url(url):
                    valid_filter_urls.append(url)
                else:
                    print(f"[FlashDeepSearch] 警告: 无效筛选URL被跳过: {url}")
              
            # 阶段1: 生成关键词
            print(f"[FlashDeepSearch] 阶段1: 为主题「{search_content}」生成{search_broadness}个关键词...")
            keywords = self.generate_keywords(search_content, search_broadness)
            print(f"[FlashDeepSearch] 生成关键词: {keywords}")
            
            if valid_urls:
                print(f"[FlashDeepSearch] 检测到{len(valid_urls)}个有效URL作为搜索源")
            
            if valid_filter_urls:
                print(f"[FlashDeepSearch] 检测到{len(valid_filter_urls)}个URL用于筛选搜索结果")
              
            # 阶段2: 执行搜索
            print(f"[FlashDeepSearch] 阶段2: 执行搜索...")
            if valid_urls:
                # 混合搜索：关键词搜索 + URL内容获取
                search_results = self.execute_mixed_searches(keywords, valid_urls, valid_filter_urls)
            else:
                # 纯关键词搜索，可能带URL筛选
                search_results = self.execute_concurrent_searches(keywords, valid_filter_urls)
            print(f"[FlashDeepSearch] 完成{len(search_results)}个搜索任务")
              
            # 阶段3: 生成报告
            print(f"[FlashDeepSearch] 阶段3: 生成研究报告...")
            report_content = self.generate_report(search_content, search_results, valid_filter_urls)
              
            # 保存报告
            saved_path = self.save_report(report_content, search_content)
            print(f"[FlashDeepSearch] 报告已保存到: {saved_path}")
            
            # 统计筛选结果
            filtered_count = sum(1 for result in search_results
                               if result.get('filtered') and result.get('filtered_count', 0) > 0)
              
            message_parts = [f"深度研究完成！已为主题「{search_content}」生成详细研究报告"]
            message_parts.append(f"包含{len(keywords)}个关键词搜索")
            if valid_urls:
                message_parts.append(f"{len(valid_urls)}个URL内容")
            if valid_filter_urls:
                message_parts.append(f"并按{len(valid_filter_urls)}个URL域名筛选结果")
            message_parts.append(f"报告已保存到{saved_path}")
            
            return {
                "status": "success",
                "result": report_content,
                "messageForAI": "，".join(message_parts) + "。",
                "metadata": {
                    "keywords_generated": len(keywords),
                    "urls_processed": len(valid_urls),
                    "filter_urls_used": len(valid_filter_urls),
                    "searches_completed": len(search_results),
                    "filtered_searches": filtered_count,
                    "saved_to": saved_path
                }
            }
        except Exception as e:
            return {
                "status": "error",
                "error": f"处理请求失败: {str(e)}"
            }


# 1. Initialize the FastMCP server with a unique name.
mcp = FastMCP("web-content-server")

# 创建深度搜索实例
deep_search_instance = FlashDeepSearchPython()

@mcp.tool()
async def hello(name: str) -> str:
    """
    Greets the user by their name.

    Args:
        name: The name of the person to greet.

    Returns:
        A personalized greeting message.
    """
    print(f"Tool 'hello' was called with name: {name}")
    return f"Hello, {name}! This message is from an MCP server instance."

@mcp.tool()
async def fetch_web_content(url: str, proxy_port: Optional[int] = None) -> Dict:
    """
    从指定URL抓取网页内容，智能提取正文和链接。
    
    这个工具能够：
    1. 自动移除广告、导航栏、侧边栏等噪音元素
    2. 智能识别并提取网页的主要内容
    3. 对于文章页面，返回完整的文本内容和相关链接
    4. 对于列表页面，主要返回链接列表
    5. 支持代理配置用于网络请求
    
    Args:
        url: 要抓取的网页URL，必须以 http:// 或 https:// 开头
        proxy_port: 可选的代理服务器端口号，如果提供则使用本地代理 127.0.0.1:proxy_port
        
    Returns:
        包含抓取结果的字典，格式如下：
        - status: "success" 或 "error"
        - content_type: "text"（文章内容）, "links"（链接列表）, 或 "mixed"（混合内容）
        - content: 实际的内容数据
        - url: 原始请求的URL
        - 如果是链接类型，还包含 link_count 字段
        - 如果出错，包含 error 字段描述错误信息
    """
    print(f"Tool 'fetch_web_content' called with URL: {url}, proxy_port: {proxy_port}")
    
    try:
        # 验证URL格式
        if not url or not (url.startswith('http://') or url.startswith('https://')):
            raise ValueError("无效的 URL 格式。URL 必须以 http:// 或 https:// 开头。")
        
        # 创建抓取器实例
        fetcher = DirectWebFetcher(proxy_port=proxy_port)
        
        # 执行抓取
        result = fetcher.fetch_url(url)
        
        # 处理空结果
        if isinstance(result, str) and not result.strip():
            result = "成功获取网页，但提取到的内容为空。"
        elif isinstance(result, list) and len(result) == 0:
            result = "成功获取网页，但提取到的内容为空。"
        
        # 根据结果类型包装返回值
        if isinstance(result, str):
            return {
                "status": "success",
                "content_type": "text",
                "content": result,
                "url": url
            }
        elif isinstance(result, list):
            return {
                "status": "success",
                "content_type": "links",
                "content": result,
                "url": url,
                "link_count": len(result)
            }
        else:
            return {
                "status": "success",
                "content_type": "mixed",
                "content": result,
                "url": url
            }
            
    except ValueError as e:
        error_msg = f"参数错误: {str(e)}"
        print(f"Fetch error: {error_msg}")
        return {
            "status": "error",
            "error": error_msg,
            "url": url
        }
    except Exception as e:
        error_msg = f"抓取失败: {str(e)}"
        print(f"Fetch error: {error_msg}")
        return {
            "status": "error",
            "error": error_msg,
            "url": url
        }

@mcp.tool()
async def deep_research(
    search_content: str,
    search_broadness: int = 10,
    search_urls: Optional[List[str]] = None,
    filter_urls: Optional[List[str]] = None
) -> Dict:
    """
    执行深度研究，生成基于多维度搜索的详细研究报告。
    
    这个工具能够：
    1. 基于研究主题自动生成多个搜索关键词
    2. 并行执行多个搜索任务（DuckDuckGo搜索 + URL内容获取）
    3. 支持按特定域名筛选搜索结果
    4. 调用AI模型生成深度研究报告
    5. 自动保存报告到本地文件
    
    Args:
        search_content: 研究主题，必填参数，描述要研究的内容
        search_broadness: 搜索广度，控制生成关键词的数量，默认10个，范围5-20
        search_urls: 可选的URL列表，作为额外的内容源进行搜索
        filter_urls: 可选的URL筛选列表，只返回匹配这些域名的搜索结果
        
    Returns:
        包含研究结果的字典，格式如下：
        - status: "success" 或 "error"
        - result: 生成的研究报告内容（Markdown格式）
        - messageForAI: 给AI的简要消息描述
        - metadata: 包含搜索统计信息的字典
          - keywords_generated: 生成的关键词数量
          - urls_processed: 处理的URL数量
          - filter_urls_used: 使用的筛选URL数量
          - searches_completed: 完成的搜索任务数量
          - filtered_searches: 经过筛选的搜索数量
          - saved_to: 报告保存路径
        - 如果出错，包含 error 字段描述错误信息
        
    注意事项：
        - 需要设置环境变量 OPENAI_API_KEY 用于AI模型调用
        - 可选设置 OPENAI_BASE_URL（默认使用OpenAI官方API）
        - 可选设置 DeepSearchModel（默认使用gpt-4o）
        - 需要安装 ddgs 或 duckduckgo-search 库用于搜索功能
    """
    print(f"Tool 'deep_research' called with content: {search_content}, broadness: {search_broadness}")
    
    try:
        # 构建请求数据
        input_data = {
            'SearchContent': search_content,
            'SearchBroadness': search_broadness,
            'SearchUrls': search_urls or [],
            'FilterUrls': filter_urls or []
        }
        
        # 处理搜索请求
        result = deep_search_instance.process_request(input_data)
        
        print(f"Deep research completed with status: {result.get('status')}")
        return result
        
    except Exception as e:
        error_msg = f"深度研究执行失败: {str(e)}"
        print(f"Deep research error: {error_msg}")
        return {
            "status": "error",
            "error": error_msg
        }

# ==================== Jira-Confluence-Dify 同步工具 ====================

if SYNC_MODULES_AVAILABLE:
    @mcp.tool()
    async def sync_all_to_dify(
        jira_project_key: Optional[str] = None,
        jira_since: str = "-30d",
        confluence_space_key: Optional[str] = None,
        confluence_since_days: int = 30
    ) -> Dict:
        """
        同步所有配置的数据源（Jira + Confluence）到 Dify 知识库
        
        这个工具会：
        1. 从 Jira 项目拉取 Issues
        2. 从 Confluence 空间拉取 Pages
        3. 对每条数据进行版本控制检查（基于更新时间）
        4. 只同步新数据或有更新的数据到 Dify
        5. 在数据库中记录同步状态和时间
        
        Args:
            jira_project_key: Jira 项目 Key（如 'PROJ'），为 None 则使用环境变量 JIRA_PROJECT_KEY
            jira_since: Jira 数据拉取时间范围，默认 '-30d'（最近30天），支持 '-7d', '-1h' 等
            confluence_space_key: Confluence 空间 Key（如 'TEAM'），为 None 则使用环境变量 CONFLUENCE_SPACE_KEY
            confluence_since_days: Confluence 数据拉取天数，默认 30 天
        
        Returns:
            包含同步结果的字典：
            - status: "success" 或 "error"
            - jira_pulled: 从 Jira 拉取的数量
            - confluence_pulled: 从 Confluence 拉取的数量
            - total_pulled: 总拉取数量
            - synced: 成功同步的数量
            - skipped: 跳过的数量（内容未变化）
            - failed: 失败的数量
            - details: 每条数据的详细同步结果
            - message: 简要消息
        
        环境变量要求：
            - ATLASSIAN_URL: Atlassian 域名
            - ATLASSIAN_EMAIL: Atlassian 邮箱
            - ATLASSIAN_API_TOKEN: Atlassian API Token
            - JIRA_PROJECT_KEY: Jira 项目 Key（如果参数未提供）
            - CONFLUENCE_SPACE_KEY: Confluence 空间 Key（如果参数未提供）
            - DIFY_API_KEY: Dify API Key
            - DIFY_API_URL: Dify API URL
            - DIFY_DATASET_ID: Dify 知识库 ID
        """
        print(f"Tool 'sync_all_to_dify' called")
        print(f"  Jira: project={jira_project_key or 'from env'}, since={jira_since}")
        print(f"  Confluence: space={confluence_space_key or 'from env'}, since_days={confluence_since_days}")
        
        try:
            result = sync_all_sources(
                jira_project_key=jira_project_key,
                jira_since=jira_since,
                confluence_space_key=confluence_space_key,
                confluence_since_days=confluence_since_days
            )
            
            print(f"Sync all completed: {result.get('message')}")
            return result
            
        except Exception as e:
            error_msg = f"全量同步失败: {str(e)}"
            print(f"Sync all error: {error_msg}")
            logging.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "error": error_msg
            }

    @mcp.tool()
    async def sync_jira_to_dify(
        project_key: Optional[str] = None,
        since: str = "-30d",
        max_results: int = 100
    ) -> Dict:
        """
        仅同步 Jira Issues 到 Dify 知识库
        
        这个工具会：
        1. 从指定 Jira 项目拉取 Issues
        2. 对每个 Issue 进行版本控制检查
        3. 只同步新 Issue 或有更新的 Issue
        4. 记录同步状态到数据库
        
        Args:
            project_key: Jira 项目 Key（如 'PROJ'），为 None 则使用环境变量 JIRA_PROJECT_KEY
            since: 拉取时间范围，默认 '-30d'，支持 '-7d', '-1h', '-2w' 等格式
            max_results: 最大拉取数量，默认 100
        
        Returns:
            包含同步结果的字典：
            - status: "success" 或 "error"
            - pulled: 从 Jira 拉取的数量
            - synced: 成功同步的数量
            - skipped: 跳过的数量
            - failed: 失败的数量
            - issues: 每个 Issue 的详细同步结果列表
            - message: 简要消息
        
        环境变量要求：
            - ATLASSIAN_URL, ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN
            - JIRA_PROJECT_KEY（如果参数未提供）
            - DIFY_API_KEY, DIFY_API_URL, DIFY_DATASET_ID
        """
        print(f"Tool 'sync_jira_to_dify' called")
        print(f"  project_key={project_key or 'from env'}, since={since}, max_results={max_results}")
        
        try:
            result = sync_jira_only(
                project_key=project_key,
                since=since,
                max_results=max_results
            )
            
            print(f"Jira sync completed: {result.get('message')}")
            return result
            
        except Exception as e:
            error_msg = f"Jira 同步失败: {str(e)}"
            print(f"Jira sync error: {error_msg}")
            logging.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "error": error_msg
            }

    @mcp.tool()
    async def sync_confluence_to_dify(
        space_key: Optional[str] = None,
        since_days: int = 30,
        max_results: int = 100
    ) -> Dict:
        """
        仅同步 Confluence Pages 到 Dify 知识库
        
        这个工具会：
        1. 从指定 Confluence 空间拉取 Pages
        2. 对每个 Page 进行版本控制检查
        3. 只同步新 Page 或有更新的 Page
        4. 记录同步状态到数据库
        
        Args:
            space_key: Confluence 空间 Key（如 'TEAM'），为 None 则使用环境变量 CONFLUENCE_SPACE_KEY
            since_days: 拉取最近 N 天的页面，默认 30 天
            max_results: 最大拉取数量，默认 100
        
        Returns:
            包含同步结果的字典：
            - status: "success" 或 "error"
            - pulled: 从 Confluence 拉取的数量
            - synced: 成功同步的数量
            - skipped: 跳过的数量
            - failed: 失败的数量
            - pages: 每个 Page 的详细同步结果列表
            - message: 简要消息
        
        环境变量要求：
            - ATLASSIAN_URL, ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN
            - CONFLUENCE_SPACE_KEY（如果参数未提供）
            - DIFY_API_KEY, DIFY_API_URL, DIFY_DATASET_ID
        """
        print(f"Tool 'sync_confluence_to_dify' called")
        print(f"  space_key={space_key or 'from env'}, since_days={since_days}, max_results={max_results}")
        
        try:
            result = sync_confluence_only(
                space_key=space_key,
                since_days=since_days,
                max_results=max_results
            )
            
            print(f"Confluence sync completed: {result.get('message')}")
            return result
            
        except Exception as e:
            error_msg = f"Confluence 同步失败: {str(e)}"
            print(f"Confluence sync error: {error_msg}")
            logging.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "error": error_msg
            }

    @mcp.tool()
    async def query_sync_records(
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50
    ) -> Dict:
        """
        查询同步历史记录
        
        这个工具可以查询数据库中的同步历史，用于：
        1. 查看哪些数据已经同步过
        2. 检查同步状态（成功/失败）
        3. 查看同步时间和 Dify 文档 ID
        4. 调试同步问题
        
        Args:
            source_type: 筛选数据源类型，可选 'JIRA' 或 'CONFLUENCE'，为 None 表示查询全部
            status: 筛选同步状态，可选 'SUCCESS' 或 'FAILED'，为 None 表示查询全部
            limit: 返回记录数量限制，默认 50 条
        
        Returns:
            包含查询结果的字典：
            - status: "success" 或 "error"
            - total_records: 数据库中符合条件的总记录数
            - returned: 本次返回的记录数
            - records: 记录列表，每条记录包含：
              - source_id: 数据源 ID（Jira Key 或 Confluence Page ID）
              - source_type: 数据源类型（JIRA/CONFLUENCE）
              - last_synced_update_time: 最后同步的远程更新时间
              - dify_document_id: Dify 文档 ID
              - last_sync_status: 最后同步状态（SUCCESS/FAILED）
              - last_synced_at: 最后同步时间
            - message: 简要消息
        
        示例：
            - 查询所有 Jira 同步记录：source_type='JIRA'
            - 查询所有失败的记录：status='FAILED'
            - 查询 Confluence 的成功记录：source_type='CONFLUENCE', status='SUCCESS'
        """
        print(f"Tool 'query_sync_records' called")
        print(f"  source_type={source_type}, status={status}, limit={limit}")
        
        try:
            result = query_sync_history(
                source_type=source_type,
                status=status,
                limit=limit
            )
            
            print(f"Query completed: {result.get('message')}")
            return result
            
        except Exception as e:
            error_msg = f"查询同步记录失败: {str(e)}"
            print(f"Query error: {error_msg}")
            logging.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "error": error_msg
            }

# ==================== End of Sync Tools ====================

# 2. Set up the SSE transport
transport = SseServerTransport("/messages/")

# Define handler functions
async def handle_sse(request):
    """Handles the SSE connection from the client."""
    async with transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp._mcp_server.run(
            streams[0], streams[1], mcp._mcp_server.create_initialization_options()
        )

# 3. Create a Starlette web application
app = Starlette(routes=[
    Route("/sse/", endpoint=handle_sse),
    Mount("/messages/", app=transport.handle_post_message),
])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the MCP Greeter Server with SSE transport.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the server to.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind the server to.")
    args = parser.parse_args()

    print(f"Starting MCP Greeter Server with SSE transport on {args.host}:{args.port}")
    print(f"SSE endpoint available at http://{args.host}:{args.port}/sse/")
    print("The server is now ready to accept HTTP connections.")

    # 4. Run the server using uvicorn
    uvicorn.run(app, host=args.host, port=args.port)

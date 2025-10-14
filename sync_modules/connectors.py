import os
import logging
import re
from datetime import datetime, timedelta
from jira import JIRA
from atlassian import Confluence
from dateutil import parser

def get_atlassian_credentials():
    """获取 Atlassian 认证信息"""
    url = os.getenv("ATLASSIAN_URL")
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("ATLASSIAN_API_TOKEN")
    
    if not all([url, email, token]) or token == "粘贴你从Atlassian官网获取的API Token":
        return None, None, None
    
    return url, email, token

def get_jira_client():
    """获取 Jira 客户端实例"""
    url, email, token = get_atlassian_credentials()
    if not url:
        return None
    
    try:
        return JIRA(server=url, basic_auth=(email, token))
    except Exception as e:
        logging.error(f"Jira 客户端初始化失败: {e}")
        return None

def get_confluence_client():
    """获取 Confluence 客户端实例"""
    url, email, token = get_atlassian_credentials()
    if not url:
        return None
    
    try:
        return Confluence(url=url, username=email, password=token)
    except Exception as e:
        logging.error(f"Confluence 客户端初始化失败: {e}")
        return None

def get_jira_issues(project_key: str, since: str = "-7d", max_results: int = 100):
    """
    获取指定 Jira 项目中在特定时间后有更新的 issues
    
    Args:
        project_key: 项目的 Key，如 'PROJ'
        since: 时间范围，如 '-1d', '-8h', '-30d'
        max_results: 最大返回数量
    
    Returns:
        一个包含 issue 信息的列表
    """
    jira_client = get_jira_client()
    if not jira_client:
        logging.warning("Jira 客户端未初始化，跳过 Jira issue 拉取")
        return []

    logging.info(f"正在从 Jira 项目 {project_key} 中拉取数据（时间范围: {since}）...")
    try:
        # JQL: Jira Query Language
        jql_query = f"project = '{project_key}' AND updated >= '{since}' ORDER BY updated DESC"
        issues = jira_client.search_issues(jql_query, maxResults=max_results)

        formatted_issues = []
        for issue in issues:
            formatted_issues.append({
                'id': issue.key,
                'type': 'JIRA',
                'updated_at': issue.fields.updated,
                'content': f"标题: {issue.fields.summary}\n\n描述: {issue.fields.description or '无'}\n\n状态: {issue.fields.status.name}"
            })
        
        logging.info(f"成功拉取 {len(formatted_issues)} 条 Jira issues")
        return formatted_issues
    except Exception as e:
        logging.error(f"从 Jira 拉取数据失败: {e}")
        return []

def get_confluence_pages(space_key: str, since_days: int = 7, max_results: int = 100):
    """
    获取指定 Confluence 空间中最近更新的页面
    
    Args:
        space_key: 空间的 Key，如 'TEAM'
        since_days: 获取最近 N 天更新的页面
        max_results: 最大返回数量
    
    Returns:
        一个包含页面信息的列表
    """
    confluence_client = get_confluence_client()
    if not confluence_client:
        logging.warning("Confluence 客户端未初始化，跳过 Confluence 页面拉取")
        return []
    
    logging.info(f"正在从 Confluence 空间 {space_key} 中拉取数据（最近 {since_days} 天）...")
    try:
        # 计算起始日期
        since_date = datetime.now() - timedelta(days=since_days)
        
        # 获取空间中的所有页面
        pages = confluence_client.get_all_pages_from_space(
            space=space_key,
            start=0,
            limit=max_results,
            expand='version,body.storage'
        )
        
        formatted_pages = []
        for page in pages:
            # 解析更新时间
            updated_at = page['version']['when']
            updated_time = parser.isoparse(updated_at)
            
            # 只包含最近更新的页面
            if updated_time >= since_date.replace(tzinfo=updated_time.tzinfo):
                # 获取页面正文（HTML 格式）
                content_html = page.get('body', {}).get('storage', {}).get('value', '')
                
                # 简单的 HTML 清理（移除标签）
                content_text = re.sub(r'<[^>]+>', ' ', content_html)
                # 清理多余空白
                content_text = re.sub(r'\s+', ' ', content_text).strip()
                
                formatted_pages.append({
                    'id': str(page['id']),
                    'type': 'CONFLUENCE',
                    'updated_at': updated_at,
                    'content': f"标题: {page['title']}\n\n内容: {content_text[:5000]}"  # 限制长度
                })
        
        logging.info(f"成功拉取 {len(formatted_pages)} 个 Confluence 页面")
        return formatted_pages
    except Exception as e:
        logging.error(f"从 Confluence 拉取数据失败: {e}")
        return []

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    # 测试 Jira 连接
    logging.info("--- 测试 Jira 连接 ---")
    jira_data = get_jira_issues(project_key='PROJ', since='-30d')
    if jira_data:
        logging.info(f"成功获取到 {len(jira_data)} 条 Jira issues")
        if jira_data:
            logging.info(f"第一条: {jira_data[0]}")
    
    # 测试 Confluence 连接
    logging.info("\n--- 测试 Confluence 连接 ---")
    confluence_space = os.getenv("CONFLUENCE_SPACE_KEY", "")
    if confluence_space and confluence_space != "YOUR_SPACE_KEY":
        confluence_data = get_confluence_pages(space_key=confluence_space, since_days=30)
        if confluence_data:
            logging.info(f"成功获取到 {len(confluence_data)} 个 Confluence 页面")
            if confluence_data:
                logging.info(f"第一个: {confluence_data[0]}")
    else:
        logging.warning("未配置 CONFLUENCE_SPACE_KEY，跳过 Confluence 测试")
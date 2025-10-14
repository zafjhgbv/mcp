import os
import requests
import logging

def get_dify_config():
    """获取 Dify 配置信息"""
    api_key = os.getenv("DIFY_API_KEY")
    api_url = os.getenv("DIFY_API_URL")
    dataset_id = os.getenv("DIFY_DATASET_ID")
    
    if not all([api_key, api_url, dataset_id]) or api_key == "粘贴你的Dify API Key":
        return None, None, None
    
    return api_key, api_url, dataset_id

def upload_document_to_dify(doc_name: str, doc_content: str):
    """
    将单个文档上传到指定的 Dify 知识库
    
    Args:
        doc_name: 文档名称（如 Jira issue Key 'PROJ-123'）
        doc_content: 文档的文本内容
    
    Returns:
        成功则返回 Dify 文档 ID，失败则返回 None
    """
    api_key, api_url, dataset_id = get_dify_config()
    if not api_key:
        logging.warning("Dify 客户端未初始化，跳过文档上传")
        return None

    logging.info(f"正在上传文档 '{doc_name}' 到 Dify...")
    
    # Dify API 使用 create_by_text 端点
    url = f"{api_url}/datasets/{dataset_id}/document/create_by_text"
    headers = {"Authorization": f"Bearer {api_key}"}

    # 准备表单数据
    data = {
        "name": doc_name,
        "text": doc_content,
        "indexing_technique": "high_quality",  # 或 "economy"
        "process_rule": {
            "mode": "automatic"
        }
    }

    try:
        # 尝试方法1：create_by_text 端点
        response = requests.post(url, headers=headers, json=data, timeout=60)
        
        if response.status_code == 404 or response.status_code == 405:
            # 如果方法1失败，尝试方法2：使用文件上传格式
            logging.info("尝试使用文件上传格式...")
            url = f"{api_url}/datasets/{dataset_id}/document/create_by_file"
            
            # 创建一个临时文本文件
            files = {
                'file': (f'{doc_name}.txt', doc_content.encode('utf-8'), 'text/plain')
            }
            form_data = {
                'indexing_technique': 'high_quality',
                'process_rule': '{"mode":"automatic"}'
            }
            
            response = requests.post(url, headers=headers, files=files, data=form_data, timeout=60)
        
        response.raise_for_status()
        result = response.json()
        
        # Dify API 返回的文档 ID 可能在不同字段
        doc_id = result.get('document', {}).get('id') or result.get('id') or result.get('document_id')
        
        if doc_id:
            logging.info(f"文档上传成功！Dify Document ID: {doc_id}")
            return doc_id
        else:
            logging.warning(f"文档可能上传成功，但未获取到 ID。响应: {result}")
            return "uploaded_without_id"
            
    except requests.exceptions.RequestException as e:
        logging.error(f"上传到 Dify 失败: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"状态码: {e.response.status_code}")
            logging.error(f"响应内容: {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"上传过程发生异常: {e}")
        return None

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    logging.info("--- 测试 Dify 连接 ---")
    test_name = "TEST-MCP-001"
    test_content = "这是一个来自 MCP Server 的测试内容，用于验证 Dify API 连接。"
    document_id = upload_document_to_dify(test_name, test_content)
    if document_id:
        logging.info(f"测试成功！文档 ID: {document_id}")
        logging.info("请到你的 Dify 知识库中查看是否存在 'TEST-MCP-001'")
    else:
        logging.error("测试失败！请检查配置")
"""
同步核心逻辑模块

整合 Jira、Confluence 数据拉取、版本控制、Dify 上传等功能
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional
from dateutil import parser as date_parser

from .database import get_sync_record, update_sync_record
from .connectors import get_jira_issues, get_confluence_pages
from .dify_client import upload_document_to_dify


def sync_single_item(item: Dict) -> Dict:
    """
    同步单个数据项的核心逻辑（包含版本控制）
    
    Args:
        item: 包含 id, type, updated_at, content 的字典
    
    Returns:
        同步结果字典，包含 status, dify_doc_id 等信息
    """
    source_id = item['id']
    source_type = item['type']
    
    try:
        # 解析远程更新时间
        remote_update_time = date_parser.isoparse(item['updated_at'])
        
        # 查询本地数据库记录
        local_record = get_sync_record(source_id)
        
        # 版本控制判断
        should_sync = False
        if not local_record:
            should_sync = True
            logging.info(f"  → 新数据: {source_id}")
        else:
            # 将数据库中的时间转换为 datetime 对象
            local_time = local_record.last_synced_update_time
            if isinstance(local_time, str):
                try:
                    local_time = date_parser.parse(local_time)
                except Exception as e:
                    logging.warning(f"  ⚠ 解析本地时间失败: {e}，将重新同步")
                    should_sync = True
            
            if not should_sync:
                # 时区处理：统一转换为 naive datetime 进行比较
                if remote_update_time.tzinfo and not local_time.tzinfo:
                    remote_update_time_naive = remote_update_time.replace(tzinfo=None)
                    should_sync = remote_update_time_naive > local_time
                elif not remote_update_time.tzinfo and local_time.tzinfo:
                    local_time_naive = local_time.replace(tzinfo=None)
                    should_sync = remote_update_time > local_time_naive
                else:
                    should_sync = remote_update_time > local_time
                
                if should_sync:
                    local_time_str = local_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(local_time, 'strftime') else str(local_time)
                    remote_time_str = remote_update_time.strftime('%Y-%m-%d %H:%M:%S')
                    logging.info(f"  → 检测到更新: {source_id} (本地: {local_time_str}, 远程: {remote_time_str})")
        
        # 如果需要同步
        if should_sync:
            # 上传到 Dify
            dify_id = upload_document_to_dify(source_id, item['content'])
            
            # 更新数据库记录
            if dify_id:
                update_sync_record(
                    source_id=source_id,
                    source_type=source_type,
                    updated_at=remote_update_time,
                    dify_doc_id=dify_id,
                    status='SUCCESS'
                )
                logging.info(f"  ✓ {source_id} 同步成功 (Dify ID: {dify_id})")
                return {
                    'id': source_id,
                    'type': source_type,
                    'status': 'synced',
                    'dify_doc_id': dify_id,
                    'message': '同步成功'
                }
            else:
                update_sync_record(
                    source_id=source_id,
                    source_type=source_type,
                    updated_at=remote_update_time,
                    dify_doc_id='',
                    status='FAILED'
                )
                logging.error(f"  ✗ {source_id} 同步失败")
                return {
                    'id': source_id,
                    'type': source_type,
                    'status': 'failed',
                    'dify_doc_id': None,
                    'message': '上传到 Dify 失败'
                }
        else:
            logging.info(f"  ⊙ {source_id} 内容未变化，跳过")
            return {
                'id': source_id,
                'type': source_type,
                'status': 'skipped',
                'dify_doc_id': local_record.dify_document_id if local_record else None,
                'message': '内容未变化'
            }
    
    except Exception as e:
        logging.error(f"  ✗ {source_id} 处理失败: {e}", exc_info=True)
        return {
            'id': source_id,
            'type': source_type,
            'status': 'error',
            'dify_doc_id': None,
            'message': f'处理失败: {str(e)}'
        }


def sync_all_sources(
    jira_project_key: Optional[str] = None,
    jira_since: str = "-30d",
    confluence_space_key: Optional[str] = None,
    confluence_since_days: int = 30,
    jira_max_results: int = 100,
    confluence_max_results: int = 100
) -> Dict:
    """
    同步所有配置的数据源（Jira + Confluence）
    
    Returns:
        包含同步统计信息的字典
    """
    import os
    
    logging.info("=" * 60)
    logging.info("开始全量同步任务 (Jira + Confluence)")
    logging.info("=" * 60)
    
    all_items = []
    jira_count = 0
    confluence_count = 0
    
    # 1. 从 Jira 拉取数据
    if not jira_project_key:
        jira_project_key = os.getenv('JIRA_PROJECT_KEY', 'PROJ')
    
    if jira_project_key and jira_project_key != 'PROJ':
        logging.info(f"\n正在同步 Jira 项目: {jira_project_key}")
        jira_items = get_jira_issues(
            project_key=jira_project_key,
            since=jira_since,
            max_results=jira_max_results
        )
        all_items.extend(jira_items)
        jira_count = len(jira_items)
        logging.info(f"Jira 拉取完成: {jira_count} 条")
    else:
        logging.info("⊗ 未配置 JIRA_PROJECT_KEY，跳过 Jira 同步")
    
    # 2. 从 Confluence 拉取数据
    if not confluence_space_key:
        confluence_space_key = os.getenv('CONFLUENCE_SPACE_KEY', '')
    
    if confluence_space_key and confluence_space_key != 'YOUR_SPACE_KEY':
        logging.info(f"\n正在同步 Confluence 空间: {confluence_space_key}")
        confluence_items = get_confluence_pages(
            space_key=confluence_space_key,
            since_days=confluence_since_days,
            max_results=confluence_max_results
        )
        all_items.extend(confluence_items)
        confluence_count = len(confluence_items)
        logging.info(f"Confluence 拉取完成: {confluence_count} 条")
    else:
        logging.info("⊗ 未配置 CONFLUENCE_SPACE_KEY，跳过 Confluence 同步")
    
    if not all_items:
        logging.info("\n✓ 没有需要同步的数据")
        return {
            'status': 'success',
            'jira_pulled': 0,
            'confluence_pulled': 0,
            'total_pulled': 0,
            'synced': 0,
            'skipped': 0,
            'failed': 0,
            'details': [],
            'message': '没有需要同步的数据'
        }
    
    logging.info(f"\n共获取 {len(all_items)} 条数据，开始版本控制检查...")
    
    # 3. 遍历每条数据执行同步
    synced_count = 0
    skipped_count = 0
    failed_count = 0
    details = []
    
    for idx, item in enumerate(all_items, 1):
        logging.info(f"[{idx}/{len(all_items)}] 正在处理 {item['type']}: {item['id']}...")
        result = sync_single_item(item)
        details.append(result)
        
        if result['status'] == 'synced':
            synced_count += 1
        elif result['status'] == 'skipped':
            skipped_count += 1
        else:
            failed_count += 1
    
    # 4. 输出统计信息
    logging.info(f"\n{'='*60}")
    logging.info("同步任务完成统计:")
    logging.info(f"  数据源: Jira({jira_count}条) + Confluence({confluence_count}条)")
    logging.info(f"  ✓ 成功同步: {synced_count} 条")
    logging.info(f"  ⊙ 跳过（无变化）: {skipped_count} 条")
    logging.info(f"  ✗ 失败: {failed_count} 条")
    logging.info(f"  总计: {len(all_items)} 条")
    logging.info(f"{'='*60}")
    
    return {
        'status': 'success',
        'jira_pulled': jira_count,
        'confluence_pulled': confluence_count,
        'total_pulled': len(all_items),
        'synced': synced_count,
        'skipped': skipped_count,
        'failed': failed_count,
        'details': details,
        'message': f'同步完成: 成功{synced_count}条, 跳过{skipped_count}条, 失败{failed_count}条'
    }


def sync_jira_only(
    project_key: Optional[str] = None,
    since: str = "-30d",
    max_results: int = 100
) -> Dict:
    """
    仅同步 Jira Issues
    
    Returns:
        包含同步统计信息的字典
    """
    import os
    
    logging.info("=" * 60)
    logging.info("开始 Jira 同步任务")
    logging.info("=" * 60)
    
    if not project_key:
        project_key = os.getenv('JIRA_PROJECT_KEY', 'PROJ')
    
    if not project_key or project_key == 'PROJ':
        return {
            'status': 'error',
            'error': '未配置 JIRA_PROJECT_KEY',
            'synced': 0,
            'skipped': 0,
            'failed': 0,
            'issues': []
        }
    
    # 拉取 Jira 数据
    logging.info(f"正在同步 Jira 项目: {project_key}")
    jira_items = get_jira_issues(project_key=project_key, since=since, max_results=max_results)
    
    if not jira_items:
        logging.info("✓ 没有需要同步的 Jira issues")
        return {
            'status': 'success',
            'pulled': 0,
            'synced': 0,
            'skipped': 0,
            'failed': 0,
            'issues': [],
            'message': '没有需要同步的数据'
        }
    
    logging.info(f"共获取 {len(jira_items)} 条 Jira issues，开始同步...")
    
    # 执行同步
    synced_count = 0
    skipped_count = 0
    failed_count = 0
    issues = []
    
    for idx, item in enumerate(jira_items, 1):
        logging.info(f"[{idx}/{len(jira_items)}] 正在处理 {item['id']}...")
        result = sync_single_item(item)
        issues.append(result)
        
        if result['status'] == 'synced':
            synced_count += 1
        elif result['status'] == 'skipped':
            skipped_count += 1
        else:
            failed_count += 1
    
    logging.info(f"\n{'='*60}")
    logging.info("Jira 同步完成:")
    logging.info(f"  ✓ 成功: {synced_count} 条")
    logging.info(f"  ⊙ 跳过: {skipped_count} 条")
    logging.info(f"  ✗ 失败: {failed_count} 条")
    logging.info(f"{'='*60}")
    
    return {
        'status': 'success',
        'pulled': len(jira_items),
        'synced': synced_count,
        'skipped': skipped_count,
        'failed': failed_count,
        'issues': issues,
        'message': f'同步完成: 成功{synced_count}条, 跳过{skipped_count}条, 失败{failed_count}条'
    }


def sync_confluence_only(
    space_key: Optional[str] = None,
    since_days: int = 30,
    max_results: int = 100
) -> Dict:
    """
    仅同步 Confluence Pages
    
    Returns:
        包含同步统计信息的字典
    """
    import os
    
    logging.info("=" * 60)
    logging.info("开始 Confluence 同步任务")
    logging.info("=" * 60)
    
    if not space_key:
        space_key = os.getenv('CONFLUENCE_SPACE_KEY', '')
    
    if not space_key or space_key == 'YOUR_SPACE_KEY':
        return {
            'status': 'error',
            'error': '未配置 CONFLUENCE_SPACE_KEY',
            'synced': 0,
            'skipped': 0,
            'failed': 0,
            'pages': []
        }
    
    # 拉取 Confluence 数据
    logging.info(f"正在同步 Confluence 空间: {space_key}")
    confluence_items = get_confluence_pages(space_key=space_key, since_days=since_days, max_results=max_results)
    
    if not confluence_items:
        logging.info("✓ 没有需要同步的 Confluence 页面")
        return {
            'status': 'success',
            'pulled': 0,
            'synced': 0,
            'skipped': 0,
            'failed': 0,
            'pages': [],
            'message': '没有需要同步的数据'
        }
    
    logging.info(f"共获取 {len(confluence_items)} 个 Confluence 页面，开始同步...")
    
    # 执行同步
    synced_count = 0
    skipped_count = 0
    failed_count = 0
    pages = []
    
    for idx, item in enumerate(confluence_items, 1):
        logging.info(f"[{idx}/{len(confluence_items)}] 正在处理 {item['id']}...")
        result = sync_single_item(item)
        pages.append(result)
        
        if result['status'] == 'synced':
            synced_count += 1
        elif result['status'] == 'skipped':
            skipped_count += 1
        else:
            failed_count += 1
    
    logging.info(f"\n{'='*60}")
    logging.info("Confluence 同步完成:")
    logging.info(f"  ✓ 成功: {synced_count} 条")
    logging.info(f"  ⊙ 跳过: {skipped_count} 条")
    logging.info(f"  ✗ 失败: {failed_count} 条")
    logging.info(f"{'='*60}")
    
    return {
        'status': 'success',
        'pulled': len(confluence_items),
        'synced': synced_count,
        'skipped': skipped_count,
        'failed': failed_count,
        'pages': pages,
        'message': f'同步完成: 成功{synced_count}条, 跳过{skipped_count}条, 失败{failed_count}条'
    }


def query_sync_history(
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    order_by: str = "last_synced_at"
) -> Dict:
    """
    查询同步历史记录
    
    Args:
        source_type: 筛选数据源类型 'JIRA' 或 'CONFLUENCE'
        status: 筛选同步状态 'SUCCESS' 或 'FAILED'
        limit: 返回记录数量限制
        order_by: 排序字段
    
    Returns:
        包含历史记录的字典
    """
    from .database import query_sync_records
    
    logging.info(f"查询同步历史记录 (类型: {source_type or '全部'}, 状态: {status or '全部'}, 限制: {limit})")
    
    try:
        records, total = query_sync_records(
            source_type=source_type,
            status=status,
            limit=limit,
            order_by=order_by
        )
        
        logging.info(f"查询完成: 共 {total} 条记录，返回 {len(records)} 条")
        
        return {
            'status': 'success',
            'total_records': total,
            'returned': len(records),
            'records': records,
            'message': f'查询成功，共 {total} 条记录'
        }
    except Exception as e:
        logging.error(f"查询失败: {e}", exc_info=True)
        return {
            'status': 'error',
            'error': str(e),
            'total_records': 0,
            'returned': 0,
            'records': []
        }
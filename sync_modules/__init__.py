"""
Jira-Confluence-Dify 同步模块

提供 Jira 和 Confluence 内容同步到 Dify 知识库的功能，
包含版本控制机制，避免重复同步。
"""

__version__ = "1.0.0"
__author__ = "Kilo Code"

from .database import setup_database, get_sync_record, update_sync_record
from .connectors import get_jira_issues, get_confluence_pages
from .dify_client import upload_document_to_dify
from .sync_core import (
    sync_all_sources,
    sync_jira_only,
    sync_confluence_only,
    query_sync_history
)

__all__ = [
    'setup_database',
    'get_sync_record',
    'update_sync_record',
    'get_jira_issues',
    'get_confluence_pages',
    'upload_document_to_dify',
    'sync_all_sources',
    'sync_jira_only',
    'sync_confluence_only',
    'query_sync_history'
]
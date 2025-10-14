from datetime import datetime
import os
import logging
from sqlalchemy import create_engine, text, inspect
from pathlib import Path

# 获取当前文件所在目录的父目录（fastmcp-server-feature-load-balancing）
BASE_DIR = Path(__file__).parent.parent

def get_database_url():
    """获取数据库 URL，优先使用环境变量，否则使用默认 SQLite"""
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url or database_url == "粘贴你的数据库连接字符串":
        # 默认使用 SQLite，数据库文件放在项目根目录
        default_db_path = BASE_DIR / 'sync_database.db'
        database_url = f"sqlite:///{default_db_path}"
        logging.info(f"使用默认 SQLite 数据库: {default_db_path}")
    
    return database_url

# 创建数据库引擎
engine = create_engine(get_database_url())

def setup_database():
    """连接数据库并创建 sync_tracker 表 (如果不存在)"""
    if not engine:
        logging.error("数据库引擎无法初始化")
        return False
        
    logging.info(f"正在连接数据库: {engine.url.drivername}...")
    try:
        with engine.connect() as connection:
            logging.info("数据库连接成功！正在检查并创建 sync_tracker 表...")
            
            # 创建表
            connection.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_tracker (
                source_id VARCHAR(255) PRIMARY KEY,
                source_type VARCHAR(50) NOT NULL,
                last_synced_update_time DATETIME NOT NULL,
                dify_document_id VARCHAR(255),
                last_sync_status VARCHAR(50),
                last_synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """))

            # 对于 SQLite，创建触发器来模拟 ON UPDATE CURRENT_TIMESTAMP
            if engine.url.drivername == 'sqlite':
                try:
                    result = connection.execute(text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND name='update_sync_tracker_modtime'"
                    )).fetchone()
                    trigger_exists = result is not None
                except:
                    trigger_exists = False

                if not trigger_exists:
                    logging.info("正在为 SQLite 创建更新时间的触发器...")
                    try:
                        connection.execute(text("""
                        CREATE TRIGGER update_sync_tracker_modtime
                        AFTER UPDATE ON sync_tracker
                        FOR EACH ROW
                        BEGIN
                            UPDATE sync_tracker
                            SET last_synced_at = CURRENT_TIMESTAMP
                            WHERE source_id = OLD.source_id;
                        END;
                        """))
                        logging.info("触发器创建成功")
                    except Exception as trigger_error:
                        logging.warning(f"创建触发器失败（可能已存在）: {trigger_error}")

            connection.commit()
            logging.info("表 'sync_tracker' 已成功创建或已存在")
            return True
    except Exception as e:
        logging.error(f"数据库操作失败: {e}", exc_info=True)
        return False

def get_sync_record(source_id: str):
    """根据 source_id 从 tracker 表中获取记录"""
    if not engine:
        return None
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT * FROM sync_tracker WHERE source_id = :id"),
                {'id': source_id}
            ).first()
            return result
    except Exception as e:
        logging.error(f"查询同步记录失败: {e}", exc_info=True)
        return None

def update_sync_record(source_id: str, source_type: str, updated_at: datetime, dify_doc_id: str, status: str):
    """插入或更新一条同步记录"""
    if not engine:
        return False
        
    upsert_stmt = None
    if engine.url.drivername == 'sqlite':
        upsert_stmt = text("""
        INSERT OR REPLACE INTO sync_tracker (source_id, source_type, last_synced_update_time, dify_document_id, last_sync_status, last_synced_at)
        VALUES (:id, :type, :time, :dify_id, :status, CURRENT_TIMESTAMP);
        """)
    else:  # PostgreSQL 或其他支持 ON CONFLICT 的数据库
        upsert_stmt = text("""
        INSERT INTO sync_tracker (source_id, source_type, last_synced_update_time, dify_document_id, last_sync_status)
        VALUES (:id, :type, :time, :dify_id, :status)
        ON CONFLICT (source_id) DO UPDATE SET
            last_synced_update_time = EXCLUDED.last_synced_update_time,
            dify_document_id = EXCLUDED.dify_document_id,
            last_sync_status = EXCLUDED.last_sync_status,
            last_synced_at = CURRENT_TIMESTAMP;
        """)

    try:
        with engine.connect() as connection:
            trans = connection.begin()
            connection.execute(upsert_stmt, {
                'id': source_id,
                'type': source_type,
                'time': updated_at,
                'dify_id': dify_doc_id,
                'status': status
            })
            trans.commit()
            return True
    except Exception as e:
        logging.error(f"更新同步记录失败: {e}", exc_info=True)
        return False

def query_sync_records(source_type: str = None, status: str = None, limit: int = 50, order_by: str = "last_synced_at"):
    """
    查询同步历史记录
    
    Args:
        source_type: 筛选数据源类型 'JIRA' 或 'CONFLUENCE'，None 表示全部
        status: 筛选同步状态 'SUCCESS' 或 'FAILED'，None 表示全部
        limit: 返回记录数量限制
        order_by: 排序字段
    
    Returns:
        records: 记录列表
        total: 总记录数
    """
    if not engine:
        return [], 0
    
    try:
        with engine.connect() as connection:
            # 构建查询条件
            where_clauses = []
            params = {}
            
            if source_type:
                where_clauses.append("source_type = :source_type")
                params['source_type'] = source_type
            
            if status:
                where_clauses.append("last_sync_status = :status")
                params['status'] = status
            
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            # 查询总数
            count_sql = f"SELECT COUNT(*) as total FROM sync_tracker WHERE {where_sql}"
            total = connection.execute(text(count_sql), params).scalar()
            
            # 查询记录
            query_sql = f"""
            SELECT * FROM sync_tracker 
            WHERE {where_sql}
            ORDER BY {order_by} DESC
            LIMIT :limit
            """
            params['limit'] = limit
            
            results = connection.execute(text(query_sql), params).fetchall()
            
            # 转换为字典列表
            records = []
            for row in results:
                records.append({
                    'source_id': row[0],
                    'source_type': row[1],
                    'last_synced_update_time': str(row[2]),
                    'dify_document_id': row[3],
                    'last_sync_status': row[4],
                    'last_synced_at': str(row[5])
                })
            
            return records, total
            
    except Exception as e:
        logging.error(f"查询同步记录失败: {e}", exc_info=True)
        return [], 0

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    setup_database()
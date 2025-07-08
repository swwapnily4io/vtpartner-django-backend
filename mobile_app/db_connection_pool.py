import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
import threading
import logging
from django.conf import settings
from django.db.utils import IntegrityError
from django.db import DatabaseError
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseConnectionPool:
    """
    A thread-safe database connection pool for PostgreSQL
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(DatabaseConnectionPool, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = True
            self.pool = None
            self.create_pool()
    
    def create_pool(self):
        """Create a connection pool using settings from Django settings"""
        try:
            db_config = settings.DATABASES['default']
            
            # Connection pool configuration
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=5,      # Minimum connections in pool
                maxconn=20,     # Maximum connections in pool
                host=db_config['HOST'],
                port=db_config['PORT'],
                database=db_config['NAME'],
                user=db_config['USER'],
                password=db_config['PASSWORD'],
                # Connection timeout settings
                connect_timeout=10,
                # Additional connection parameters
                sslmode='require' if 'rds.amazonaws.com' in db_config['HOST'] else 'prefer',
                application_name='vtpartner_backend'
            )
            
            logger.info("Database connection pool created successfully")
            
        except Exception as e:
            logger.error(f"Error creating database connection pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        """
        Context manager to get a connection from the pool
        """
        connection = None
        try:
            # Get connection from pool
            connection = self.pool.getconn()
            if connection:
                # Set autocommit to False for transaction control
                connection.autocommit = False
                yield connection
        except Exception as e:
            if connection:
                connection.rollback()
            logger.error(f"Error getting connection from pool: {e}")
            raise
        finally:
            if connection:
                # Return connection to pool
                self.pool.putconn(connection)
    
    def execute_query(self, query, params=None, fetch_type='all'):
        """
        Execute a query using connection pool
        
        Args:
            query (str): SQL query to execute
            params (tuple): Query parameters
            fetch_type (str): 'all', 'one', 'none' for fetchall, fetchone, or no fetch
            
        Returns:
            Query results based on fetch_type
        """
        if params is None:
            params = ()
            
        logger.info(f"Executing query: {query}")
        logger.info(f"With parameters: {params}")
        
        with self.get_connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    
                    if fetch_type == 'all':
                        result = cursor.fetchall()
                    elif fetch_type == 'one':
                        result = cursor.fetchone()
                    else:
                        result = cursor.rowcount
                    
                    # Commit for INSERT, UPDATE, DELETE operations
                    if query.strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
                        conn.commit()
                    
                    logger.info(f"Query executed successfully. Result: {result}")
                    return result
                    
            except IntegrityError as e:
                conn.rollback()
                logger.error(f"Integrity Error: {e}")
                raise
            except Exception as e:
                conn.rollback()
                logger.error(f"Error executing query: {e}")
                raise
    
    def execute_returning_query(self, query, params=None):
        """
        Execute a query that returns data (like INSERT with RETURNING clause)
        """
        if params is None:
            params = ()
            
        logger.info(f"Executing returning query: {query}")
        logger.info(f"With parameters: {params}")
        
        with self.get_connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    
                    # Check if query returns data
                    if cursor.description:
                        result = cursor.fetchall()
                        conn.commit()
                        return result
                    else:
                        conn.commit()
                        return cursor.rowcount
                        
            except IntegrityError as e:
                conn.rollback()
                logger.error(f"Integrity Error: {e}")
                raise
            except Exception as e:
                conn.rollback()
                logger.error(f"Error executing returning query: {e}")
                raise
    
    def close_pool(self):
        """Close all connections in the pool"""
        if self.pool:
            self.pool.closeall()
            logger.info("Database connection pool closed")
    
    def get_pool_status(self):
        """Get current pool status"""
        if self.pool:
            return {
                'total_connections': len(self.pool._pool) + len(self.pool._used),
                'available_connections': len(self.pool._pool),
                'used_connections': len(self.pool._used)
            }
        return {'status': 'Pool not initialized'}

# Global pool instance
db_pool = DatabaseConnectionPool()

# Common database functions using connection pool
def select_query_pool(query, params=None):
    """
    Executes a parameterized SQL select query using connection pool
    
    Args:
        query (str): The SQL query to execute
        params (list or tuple): Parameters to substitute into the query
        
    Returns:
        list: Rows from the query result
    """
    try:
        result = db_pool.execute_query(query, params, fetch_type='all')
        return result
    except Exception as e:
        logger.error(f"Error in select_query_pool: {e}")
        raise

def insert_query_pool(query, params=None):
    """
    Executes an INSERT query using connection pool
    
    Args:
        query (str): The SQL query to execute
        params (list or tuple): Parameters to substitute into the query
        
    Returns:
        int or list: Number of affected rows or returned data if RETURNING clause
    """
    try:
        result = db_pool.execute_returning_query(query, params)
        return result
    except Exception as e:
        logger.error(f"Error in insert_query_pool: {e}")
        raise

def update_query_pool(query, params=None):
    """
    Executes an UPDATE query using connection pool
    
    Args:
        query (str): The SQL query to execute
        params (list or tuple): Parameters to substitute into the query
        
    Returns:
        int: Number of affected rows
    """
    try:
        result = db_pool.execute_query(query, params, fetch_type='none')
        return result
    except Exception as e:
        logger.error(f"Error in update_query_pool: {e}")
        raise

def delete_query_pool(query, params=None):
    """
    Executes a DELETE query using connection pool
    
    Args:
        query (str): The SQL query to execute
        params (list or tuple): Parameters to substitute into the query
        
    Returns:
        int: Number of affected rows
    """
    try:
        result = db_pool.execute_query(query, params, fetch_type='none')
        return result
    except Exception as e:
        logger.error(f"Error in delete_query_pool: {e}")
        raise

def get_pool_status():
    """Get current connection pool status"""
    return db_pool.get_pool_status()

def close_connection_pool():
    """Close the connection pool"""
    db_pool.close_pool()

# Context manager for transactions
@contextmanager
def database_transaction():
    """
    Context manager for database transactions
    Usage:
        with database_transaction() as conn:
            # Execute multiple queries
            pass
    """
    with db_pool.get_connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction error: {e}")
            raise 
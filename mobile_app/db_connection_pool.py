import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
import threading
import logging
from django.conf import settings
from django.db.utils import IntegrityError
from django.db import DatabaseError
import time
import random

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseConnectionPool:
    """
    A thread-safe database connection pool for PostgreSQL with improved connection management
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
            self.pool_creation_attempts = 0
            self.max_pool_creation_attempts = 3
            self.create_pool()
    
    def create_pool(self):
        """Create a connection pool using settings from Django settings with retry logic"""
        while self.pool_creation_attempts < self.max_pool_creation_attempts:
            try:
                db_config = settings.DATABASES['default']
                
                # Enhanced connection pool configuration
                self.pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=10,     # Increased minimum connections
                    maxconn=50,     # Increased maximum connections
                    host=db_config['HOST'],
                    port=db_config['PORT'],
                    database=db_config['NAME'],
                    user=db_config['USER'],
                    password=db_config['PASSWORD'],
                    # Enhanced connection timeout settings
                    connect_timeout=30,
                    keepalives_idle=600,
                    keepalives_interval=30,
                    keepalives_count=3,
                    # Additional connection parameters
                    sslmode='require' if 'rds.amazonaws.com' in db_config['HOST'] else 'prefer',
                    application_name='vtpartner_backend_pool'
                )
                
                logger.info(f"Database connection pool created successfully with {self.pool.minconn}-{self.pool.maxconn} connections")
                self.pool_creation_attempts = 0
                return
                
            except Exception as e:
                self.pool_creation_attempts += 1
                logger.error(f"Error creating database connection pool (attempt {self.pool_creation_attempts}): {e}")
                if self.pool_creation_attempts >= self.max_pool_creation_attempts:
                    logger.critical("Failed to create connection pool after maximum attempts")
                    raise
                time.sleep(2 ** self.pool_creation_attempts)  # Exponential backoff
    
    def recreate_pool(self):
        """Recreate the connection pool if it becomes unusable"""
        logger.warning("Recreating connection pool due to errors")
        try:
            if self.pool:
                self.pool.closeall()
        except Exception as e:
            logger.error(f"Error closing old pool: {e}")
        
        self.pool = None
        self.pool_creation_attempts = 0
        self.create_pool()
    
    @contextmanager
    def get_connection(self, timeout=30, retry_count=3):
        """
        Context manager to get a connection from the pool with timeout and retry logic
        """
        connection = None
        attempt = 0
        
        while attempt < retry_count:
            try:
                if not self.pool:
                    logger.warning("Pool is None, recreating...")
                    self.recreate_pool()
                
                # Get connection from pool with timeout
                connection = self.pool.getconn()
                
                if connection:
                    # Test the connection before using it
                    if connection.closed:
                        logger.warning("Got closed connection from pool, removing it")
                        self.pool.putconn(connection, close=True)
                        connection = None
                        raise Exception("Connection was closed")
                    
                    # Set autocommit to False for transaction control
                    connection.autocommit = False
                    yield connection
                    return
                else:
                    raise Exception("Failed to get connection from pool")
                    
            except Exception as e:
                attempt += 1
                logger.warning(f"Connection attempt {attempt} failed: {e}")
                
                if connection:
                    try:
                        connection.rollback()
                        self.pool.putconn(connection, close=True)
                    except:
                        pass
                    connection = None
                
                if attempt >= retry_count:
                    logger.error(f"Failed to get connection after {retry_count} attempts")
                    # If pool is exhausted, try to recreate it
                    if "pool exhausted" in str(e).lower():
                        logger.warning("Pool exhausted, attempting to recreate pool")
                        self.recreate_pool()
                    raise
                
                # Wait before retrying with jitter
                wait_time = min(2 ** attempt, 10) + random.uniform(0, 1)
                time.sleep(wait_time)
        
        # This should never be reached, but just in case
        raise Exception("Unable to get connection from pool after all attempts")
    
    def execute_query(self, query, params=None, fetch_type='all', timeout=30):
        """
        Execute a query using connection pool with improved error handling
        """
        if params is None:
            params = ()
            
        logger.info(f"Executing query: {query}")
        logger.info(f"With parameters: {params}")
        
        try:
            with self.get_connection(timeout=timeout) as conn:
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
                    
                    logger.info(f"Query executed successfully. Result count: {len(result) if isinstance(result, list) else result}")
                    return result
                    
        except IntegrityError as e:
            logger.error(f"Integrity Error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error executing query: {e}")
            raise
        finally:
            # Ensure connection is returned to pool
            if 'conn' in locals():
                try:
                    self.pool.putconn(conn)
                except:
                    pass
    
    def execute_returning_query(self, query, params=None, timeout=30):
        """
        Execute a query that returns data (like INSERT with RETURNING clause)
        """
        if params is None:
            params = ()
            
        logger.info(f"Executing returning query: {query}")
        logger.info(f"With parameters: {params}")
        
        try:
            with self.get_connection(timeout=timeout) as conn:
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
            logger.error(f"Integrity Error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error executing returning query: {e}")
            raise
        finally:
            # Ensure connection is returned to pool
            if 'conn' in locals():
                try:
                    self.pool.putconn(conn)
                except:
                    pass
    
    def close_pool(self):
        """Close all connections in the pool"""
        if self.pool:
            try:
                self.pool.closeall()
                logger.info("Database connection pool closed")
            except Exception as e:
                logger.error(f"Error closing pool: {e}")
    
    def get_pool_status(self):
        """Get current pool status with detailed information"""
        if self.pool:
            try:
                total_connections = len(self.pool._pool) + len(self.pool._used)
                available_connections = len(self.pool._pool)
                used_connections = len(self.pool._used)
                
                return {
                    'total_connections': total_connections,
                    'available_connections': available_connections,
                    'used_connections': used_connections,
                    'pool_utilization': f"{(used_connections / total_connections * 100):.1f}%" if total_connections > 0 else "0%",
                    'pool_healthy': available_connections > 0
                }
            except Exception as e:
                logger.error(f"Error getting pool status: {e}")
                return {'status': 'Error getting pool status', 'error': str(e)}
        return {'status': 'Pool not initialized'}
    
    def cleanup_stale_connections(self):
        """Clean up stale connections in the pool"""
        if not self.pool:
            return
        
        try:
            # Get all used connections and check if they're stale
            stale_connections = []
            for conn in list(self.pool._used):
                try:
                    if conn.closed:
                        stale_connections.append(conn)
                except:
                    stale_connections.append(conn)
            
            # Remove stale connections
            for conn in stale_connections:
                try:
                    self.pool._used.remove(conn)
                    logger.info("Removed stale connection from pool")
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"Error cleaning up stale connections: {e}")

# Global pool instance
db_pool = DatabaseConnectionPool()

# Enhanced database functions with fallback mechanisms
def select_query_pool(query, params=None, timeout=30, retry_count=2):
    """
    Executes a parameterized SQL select query using connection pool with retry logic
    """
    attempt = 0
    while attempt < retry_count:
        try:
            result = db_pool.execute_query(query, params, fetch_type='all', timeout=timeout)
            return result
        except Exception as e:
            attempt += 1
            logger.warning(f"select_query_pool attempt {attempt} failed: {e}")
            
            if attempt >= retry_count:
                logger.error(f"select_query_pool failed after {retry_count} attempts")
                raise
            
            # Clean up stale connections before retry
            db_pool.cleanup_stale_connections()
            time.sleep(1)

def insert_query_pool(query, params=None, timeout=30, retry_count=2):
    """
    Executes an INSERT query using connection pool with retry logic
    """
    attempt = 0
    while attempt < retry_count:
        try:
            result = db_pool.execute_returning_query(query, params, timeout=timeout)
            return result
        except Exception as e:
            attempt += 1
            logger.warning(f"insert_query_pool attempt {attempt} failed: {e}")
            
            if attempt >= retry_count:
                logger.error(f"insert_query_pool failed after {retry_count} attempts")
                raise
            
            # Clean up stale connections before retry
            db_pool.cleanup_stale_connections()
            time.sleep(1)

def update_query_pool(query, params=None, timeout=30, retry_count=2):
    """
    Executes an UPDATE query using connection pool with retry logic
    """
    attempt = 0
    while attempt < retry_count:
        try:
            result = db_pool.execute_query(query, params, fetch_type='none', timeout=timeout)
            return result
        except Exception as e:
            attempt += 1
            logger.warning(f"update_query_pool attempt {attempt} failed: {e}")
            
            if attempt >= retry_count:
                logger.error(f"update_query_pool failed after {retry_count} attempts")
                raise
            
            # Clean up stale connections before retry
            db_pool.cleanup_stale_connections()
            time.sleep(1)

def delete_query_pool(query, params=None, timeout=30, retry_count=2):
    """
    Executes a DELETE query using connection pool with retry logic
    """
    attempt = 0
    while attempt < retry_count:
        try:
            result = db_pool.execute_query(query, params, fetch_type='none', timeout=timeout)
            return result
        except Exception as e:
            attempt += 1
            logger.warning(f"delete_query_pool attempt {attempt} failed: {e}")
            
            if attempt >= retry_count:
                logger.error(f"delete_query_pool failed after {retry_count} attempts")
                raise
            
            # Clean up stale connections before retry
            db_pool.cleanup_stale_connections()
            time.sleep(1)

def get_pool_status():
    """Get current connection pool status"""
    return db_pool.get_pool_status()

def close_connection_pool():
    """Close the connection pool"""
    db_pool.close_pool()

def cleanup_pool():
    """Clean up stale connections in the pool"""
    db_pool.cleanup_stale_connections()

# Context manager for transactions
@contextmanager
def database_transaction():
    """
    Context manager for database transactions with improved error handling
    """
    with db_pool.get_connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except:
                pass
            logger.error(f"Transaction error: {e}")
            raise

# Pool maintenance function
def maintain_pool():
    """
    Perform pool maintenance tasks
    """
    try:
        # Clean up stale connections
        db_pool.cleanup_stale_connections()
        
        # Log pool status
        status = get_pool_status()
        logger.info(f"Pool status: {status}")
        
        # If pool utilization is high, log warning
        if status.get('pool_healthy') is False:
            logger.warning("Pool may be under stress, consider increasing pool size")
            
    except Exception as e:
        logger.error(f"Error during pool maintenance: {e}")

# Emergency pool recreation
def recreate_pool_if_needed():
    """
    Recreate the pool if it's in a bad state
    """
    try:
        status = get_pool_status()
        if not status.get('pool_healthy', True):
            logger.warning("Pool appears unhealthy, recreating...")
            db_pool.recreate_pool()
    except Exception as e:
        logger.error(f"Error checking/recreating pool: {e}")
        try:
            db_pool.recreate_pool()
        except Exception as e2:
            logger.critical(f"Failed to recreate pool: {e2}") 
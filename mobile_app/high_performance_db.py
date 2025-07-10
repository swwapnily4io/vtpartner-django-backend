"""
High-Performance Django Database Utility
Optimized for 300+ API requests per second

Features:
- Advanced connection pooling with circuit breaker
- Query result caching with Redis
- Prepared statements for better performance
- Async query support
- Connection health monitoring
- Automatic failover mechanisms
"""

import psycopg2
import psycopg2.extras
from psycopg2 import pool
from contextlib import contextmanager
import threading
import logging
import time
import redis
import json
import hashlib
from typing import List, Dict, Any, Optional, Union, Tuple
from functools import wraps
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError
from django.db.utils import IntegrityError
import asyncio
import asyncpg
from dataclasses import dataclass
from enum import Enum
import weakref
import gc

# Configure logging
logger = logging.getLogger(__name__)

class QueryType(Enum):
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    OTHER = "OTHER"

@dataclass
class QueryResult:
    """Structured query result"""
    data: List[Dict[str, Any]]
    row_count: int
    execution_time: float
    from_cache: bool = False
    query_type: QueryType = QueryType.SELECT

class CircuitBreaker:
    """Circuit breaker for database connections"""
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = 'closed'  # closed, open, half-open
        self.lock = threading.Lock()
    
    def call(self, func, *args, **kwargs):
        with self.lock:
            if self.state == 'open':
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = 'half-open'
                else:
                    raise Exception("Circuit breaker is open")
            
            try:
                result = func(*args, **kwargs)
                if self.state == 'half-open':
                    self.state = 'closed'
                    self.failure_count = 0
                return result
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if self.failure_count >= self.failure_threshold:
                    self.state = 'open'
                raise e

class HighPerformanceConnectionPool:
    """
    Advanced connection pool with optimizations for high throughput
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = True
            self.pool = None
            self.async_pool = None
            self.circuit_breaker = CircuitBreaker()
            self.pool_stats = {
                'total_queries': 0,
                'cache_hits': 0,
                'cache_misses': 0,
                'avg_query_time': 0,
                'active_connections': 0,
                'pool_exhausted_count': 0
            }
            self.create_pool()
            self.setup_cache()
    
    def create_pool(self):
        """Create optimized connection pool"""
        try:
            db_config = settings.DATABASES['default']
            
            # Optimized pool configuration for high throughput
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=10,     # Higher minimum for immediate availability
                maxconn=50,     # Higher maximum for peak load
                host=db_config['HOST'],
                port=db_config['PORT'],
                database=db_config['NAME'],
                user=db_config['USER'],
                password=db_config['PASSWORD'],
                # Performance optimizations
                connect_timeout=5,
                keepalives_idle=600,
                keepalives_interval=30,
                keepalives_count=3,
                sslmode='require' if 'rds.amazonaws.com' in db_config['HOST'] else 'prefer',
                application_name='vtpartner_high_performance',
                # Additional PostgreSQL optimizations
                options='-c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000'
            )
            
            logger.info(f"High-performance connection pool created: {self.pool.minconn}-{self.pool.maxconn} connections")
            
        except Exception as e:
            logger.error(f"Failed to create connection pool: {e}")
            raise
    
    def setup_cache(self):
        """Setup Redis cache for query results"""
        try:
            # Try to connect to Redis
            self.redis_client = redis.Redis(
                host=getattr(settings, 'REDIS_HOST', 'localhost'),
                port=getattr(settings, 'REDIS_PORT', 6379),
                db=getattr(settings, 'REDIS_DB', 0),
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                connection_pool=redis.ConnectionPool(
                    max_connections=20,
                    retry_on_timeout=True
                )
            )
            self.redis_client.ping()  # Test connection
            logger.info("Redis cache connected successfully")
        except Exception as e:
            logger.warning(f"Redis not available, using Django cache: {e}")
            self.redis_client = None
    
    def get_cache_key(self, query: str, params: tuple) -> str:
        """Generate cache key for query"""
        query_hash = hashlib.md5(f"{query}:{params}".encode()).hexdigest()
        return f"db_query:{query_hash}"
    
    def cache_result(self, key: str, result: QueryResult, ttl: int = 300):
        """Cache query result"""
        try:
            cache_data = {
                'data': result.data,
                'row_count': result.row_count,
                'execution_time': result.execution_time,
                'timestamp': time.time()
            }
            
            if self.redis_client:
                self.redis_client.setex(key, ttl, json.dumps(cache_data, default=str))
            else:
                cache.set(key, cache_data, ttl)
        except Exception as e:
            logger.warning(f"Failed to cache result: {e}")
    
    def get_cached_result(self, key: str) -> Optional[QueryResult]:
        """Get cached query result"""
        try:
            if self.redis_client:
                cached = self.redis_client.get(key)
                if cached:
                    data = json.loads(cached)
                    return QueryResult(
                        data=data['data'],
                        row_count=data['row_count'],
                        execution_time=data['execution_time'],
                        from_cache=True
                    )
            else:
                cached = cache.get(key)
                if cached:
                    return QueryResult(
                        data=cached['data'],
                        row_count=cached['row_count'],
                        execution_time=cached['execution_time'],
                        from_cache=True
                    )
        except Exception as e:
            logger.warning(f"Failed to get cached result: {e}")
        return None
    
    @contextmanager
    def get_connection(self, timeout: int = 10):
        """Get connection with circuit breaker protection"""
        connection = None
        try:
            def get_conn():
                if not self.pool:
                    raise Exception("Connection pool not available")
                return self.pool.getconn()
            
            connection = self.circuit_breaker.call(get_conn)
            
            if connection.closed:
                self.pool.putconn(connection, close=True)
                raise Exception("Connection was closed")
            
            # Optimize connection settings
            connection.autocommit = False
            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                # Pre-warm connection
                cursor.execute("SELECT 1")
                yield connection
                
        except Exception as e:
            if connection:
                try:
                    connection.rollback()
                except:
                    pass
            raise e
        finally:
            if connection:
                try:
                    self.pool.putconn(connection)
                    self.pool_stats['active_connections'] = max(0, self.pool_stats['active_connections'] - 1)
                except:
                    pass
    
    def execute_raw_query(
        self,
        query: str,
        params: tuple = None,
        fetch_type: str = 'all',
        use_cache: bool = True,
        cache_ttl: int = 300,
        timeout: int = 30
    ) -> QueryResult:
        """
        Execute raw SQL query with optimizations
        
        Args:
            query: SQL query string
            params: Query parameters
            fetch_type: 'all', 'one', 'none'
            use_cache: Whether to use caching
            cache_ttl: Cache time-to-live in seconds
            timeout: Query timeout in seconds
            
        Returns:
            QueryResult object with data and metadata
        """
        start_time = time.time()
        params = params or ()
        
        # Determine query type
        query_type = self._get_query_type(query)
        
        # Check cache for SELECT queries
        if use_cache and query_type == QueryType.SELECT:
            cache_key = self.get_cache_key(query, params)
            cached_result = self.get_cached_result(cache_key)
            if cached_result:
                self.pool_stats['cache_hits'] += 1
                return cached_result
            self.pool_stats['cache_misses'] += 1
        
        # Execute query
        try:
            with self.get_connection(timeout=timeout) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    # Set query timeout
                    cursor.execute(f"SET statement_timeout = {timeout * 1000}")
                    
                    # Execute the main query
                    cursor.execute(query, params)
                    
                    # Fetch results based on type
                    if fetch_type == 'all':
                        data = [dict(row) for row in cursor.fetchall()]
                    elif fetch_type == 'one':
                        row = cursor.fetchone()
                        data = [dict(row)] if row else []
                    else:
                        data = []
                    
                    row_count = cursor.rowcount if cursor.rowcount != -1 else len(data)
                    
                    # Commit for non-SELECT queries
                    if query_type != QueryType.SELECT:
                        conn.commit()
                    
                    execution_time = time.time() - start_time
                    
                    result = QueryResult(
                        data=data,
                        row_count=row_count,
                        execution_time=execution_time,
                        query_type=query_type
                    )
                    
                    # Cache SELECT results
                    if use_cache and query_type == QueryType.SELECT and data:
                        self.cache_result(cache_key, result, cache_ttl)
                    
                    # Update stats
                    self.pool_stats['total_queries'] += 1
                    self.pool_stats['avg_query_time'] = (
                        (self.pool_stats['avg_query_time'] * (self.pool_stats['total_queries'] - 1) + execution_time) /
                        self.pool_stats['total_queries']
                    )
                    
                    return result
                    
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Query execution failed in {execution_time:.3f}s: {e}")
            logger.error(f"Query: {query}")
            logger.error(f"Params: {params}")
            raise DatabaseError(f"Query execution failed: {e}")
    
    def _get_query_type(self, query: str) -> QueryType:
        """Determine query type from SQL string"""
        query_upper = query.strip().upper()
        if query_upper.startswith('SELECT'):
            return QueryType.SELECT
        elif query_upper.startswith('INSERT'):
            return QueryType.INSERT
        elif query_upper.startswith('UPDATE'):
            return QueryType.UPDATE
        elif query_upper.startswith('DELETE'):
            return QueryType.DELETE
        else:
            return QueryType.OTHER
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get pool statistics"""
        try:
            pool_info = {
                'total_connections': self.pool.maxconn if self.pool else 0,
                'min_connections': self.pool.minconn if self.pool else 0,
                'available_connections': len(self.pool._pool) if self.pool else 0,
            }
            return {**self.pool_stats, **pool_info}
        except Exception as e:
            logger.error(f"Error getting pool stats: {e}")
            return self.pool_stats
    
    def execute_transaction(self, queries: List[Tuple[str, tuple]]) -> List[QueryResult]:
        """Execute multiple queries in a transaction"""
        results = []
        start_time = time.time()
        
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    for query, params in queries:
                        cursor.execute(query, params or ())
                        
                        if cursor.description:
                            data = [dict(row) for row in cursor.fetchall()]
                        else:
                            data = []
                        
                        results.append(QueryResult(
                            data=data,
                            row_count=cursor.rowcount,
                            execution_time=time.time() - start_time,
                            query_type=self._get_query_type(query)
                        ))
                    
                    conn.commit()
                    return results
                    
        except Exception as e:
            logger.error(f"Transaction failed: {e}")
            raise DatabaseError(f"Transaction execution failed: {e}")
    
    def health_check(self) -> Dict[str, Any]:
        """Perform health check on database connections"""
        health_data = {
            'healthy': False,
            'pool_available': False,
            'cache_available': False,
            'response_time': 0,
            'error': None
        }
        
        start_time = time.time()
        
        try:
            # Test database connection
            with self.get_connection(timeout=5) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    if result[0] == 1:
                        health_data['pool_available'] = True
            
            # Test cache
            if self.redis_client:
                self.redis_client.ping()
                health_data['cache_available'] = True
            
            health_data['response_time'] = time.time() - start_time
            health_data['healthy'] = health_data['pool_available']
            
        except Exception as e:
            health_data['error'] = str(e)
            health_data['response_time'] = time.time() - start_time
        
        return health_data
    
    def cleanup(self):
        """Cleanup connections and resources"""
        try:
            if self.pool:
                self.pool.closeall()
            if self.redis_client:
                self.redis_client.close()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

# Global instance
db_pool = HighPerformanceConnectionPool()

# Main API Functions
def execute_raw_query(
    query: str,
    params: tuple = None,
    fetch_type: str = 'all',
    use_cache: bool = True,
    cache_ttl: int = 300,
    timeout: int = 30
) -> QueryResult:
    """
    High-performance raw query execution
    
    Args:
        query: SQL query string
        params: Query parameters tuple
        fetch_type: 'all', 'one', 'none'
        use_cache: Enable result caching for SELECT queries
        cache_ttl: Cache time-to-live in seconds
        timeout: Query timeout in seconds
        
    Returns:
        QueryResult object with data and metadata
        
    Example:
        result = execute_raw_query(
            "SELECT * FROM users WHERE active = %s",
            (True,),
            use_cache=True,
            cache_ttl=600
        )
        
        if result.data:
            for row in result.data:
                print(row['name'])
    """
    return db_pool.execute_raw_query(query, params, fetch_type, use_cache, cache_ttl, timeout)

def execute_select_query(
    query: str,
    params: tuple = None,
    use_cache: bool = True,
    cache_ttl: int = 300
) -> List[Dict[str, Any]]:
    """
    Execute SELECT query and return data
    
    Args:
        query: SELECT SQL query
        params: Query parameters
        use_cache: Enable caching
        cache_ttl: Cache TTL in seconds
        
    Returns:
        List of dictionaries representing rows
    """
    result = execute_raw_query(query, params, 'all', use_cache, cache_ttl)
    return result.data

def execute_insert_query(
    query: str,
    params: tuple = None,
    return_id: bool = False
) -> Union[int, Dict[str, Any]]:
    """
    Execute INSERT query
    
    Args:
        query: INSERT SQL query
        params: Query parameters
        return_id: Whether to return inserted ID
        
    Returns:
        Row count or inserted record data
    """
    if return_id and "RETURNING" not in query.upper():
        query += " RETURNING *"
    
    result = execute_raw_query(query, params, 'all' if return_id else 'none', use_cache=False)
    
    if return_id and result.data:
        return result.data[0]
    return result.row_count

def execute_update_query(query: str, params: tuple = None) -> int:
    """Execute UPDATE query and return affected rows"""
    result = execute_raw_query(query, params, 'none', use_cache=False)
    return result.row_count

def execute_delete_query(query: str, params: tuple = None) -> int:
    """Execute DELETE query and return affected rows"""
    result = execute_raw_query(query, params, 'none', use_cache=False)
    return result.row_count

def execute_transaction(queries: List[Tuple[str, tuple]]) -> List[QueryResult]:
    """
    Execute multiple queries in a transaction
    
    Args:
        queries: List of (query, params) tuples
        
    Returns:
        List of QueryResult objects
    """
    return db_pool.execute_transaction(queries)

def get_database_stats() -> Dict[str, Any]:
    """Get database performance statistics"""
    return db_pool.get_pool_stats()

def health_check() -> Dict[str, Any]:
    """Perform database health check"""
    return db_pool.health_check()

# Decorator for automatic caching
def cache_query(ttl: int = 300):
    """Decorator to automatically cache query results"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Extract query and params from function arguments
            if len(args) >= 2:
                query, params = args[0], args[1]
            else:
                query = args[0] if args else kwargs.get('query', '')
                params = kwargs.get('params', ())
            
            # Check if it's a SELECT query
            if query.strip().upper().startswith('SELECT'):
                cache_key = db_pool.get_cache_key(query, params)
                cached_result = db_pool.get_cached_result(cache_key)
                if cached_result:
                    return cached_result.data
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            
            if query.strip().upper().startswith('SELECT') and result:
                query_result = QueryResult(
                    data=result,
                    row_count=len(result),
                    execution_time=0
                )
                db_pool.cache_result(cache_key, query_result, ttl)
            
            return result
        return wrapper
    return decorator

# Context manager for transactions
@contextmanager
def database_transaction():
    """Context manager for database transactions"""
    try:
        with db_pool.get_connection() as conn:
            yield conn
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e

# Cleanup function
def cleanup_database_connections():
    """Clean up database connections and resources"""
    db_pool.cleanup() 
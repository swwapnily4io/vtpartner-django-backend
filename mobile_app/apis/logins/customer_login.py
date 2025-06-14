from django.shortcuts import render,redirect, reverse
from django.db import connection, DatabaseError
from django.views.decorators.cache import never_cache
from django.contrib.auth import logout
from django.http import HttpResponseServerError,JsonResponse,HttpResponse,HttpResponseRedirect
from colorama import Fore, Style
from django.views.decorators.cache import never_cache
from django.utils import timezone
from datetime import datetime
import pytz
import base64
import uuid
import mimetypes
import requests
import json
import time
import re
import random
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_exempt
from django.db.utils import IntegrityError
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from google.oauth2 import service_account
import google.auth.transport.requests
import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import boto3
from botocore.exceptions import ClientError
# Load environment variables from the root directory

import configparser

def load_query_mappings():
    config = configparser.ConfigParser()
    config.read('query_mapping.ini')
    return config['DEFAULT'] if 'DEFAULT' in config else {}

    
def check_missing_fields(fields):
    # Only consider a field missing if its value is None
    missing_fields = [field for field, value in fields.items() if value is None]
    print("missing_fields::", missing_fields)
    return missing_fields if missing_fields else None


#Common Functions 
def select_query(query, params=None):
    """
    Executes a parameterized SQL select query and returns the result.
    
    Args:
        query (str): The SQL query to execute.
        params (list or tuple): Parameters to substitute into the query.

    Returns:
        list: Rows from the query result.

    Raises:
        ValueError: If no data is found.
        DatabaseError: For database-specific errors.
    """
    try:
        print("Select_Query::=>", query)
        print("Params::", params)
        
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            result = None
            result = cursor.fetchall()

            # if result == []:
            #     raise ValueError("No Data Found")  # Custom error when no results are found
            print("result::",result)
            return result

    except ValueError as e:
        print(f"Error: {e}")
        raise  # Re-raise to be handled by calling function
    
    except DatabaseError as e:
        print("DatabaseError executing query:", e)
        raise  # Re-raise to be handled by calling function

    except Exception as e:
        print("Unexpected error:", e)
        raise  # Re-raise for unexpected errors
    
def insert_query2(query, params=None):
    if params is None:
        params = ()  # Default to empty tuple if no params are passed
    
    print("Executing insert query:", query)
    print("With parameters:", params)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            
            # If the query has a RETURNING clause, fetch the returned rows
            if cursor.description:
                result = cursor.fetchall()  # Fetch all returned rows if any
                connection.commit()  # Commit after insertion
                return result
            else:
                connection.commit()  # Commit if only affecting rows
                return cursor.rowcount  # Return number of affected rows
    
    except IntegrityError as e:
        print("Integrity Error: Failed to insert data due to integrity error", e)
        raise
    except Exception as e:
        print("General Error executing query:", e)
        raise


def update_query(query, params):
    print("update query::",query)
    print("update query params::",params)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.rowcount

def delete_query(query, params):
    print("delete query::",query)
    print("delete query params::",params)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.rowcount

def insert_query(query, params):
    print("Executing insert query:", query)
    print("With parameters:", params)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            
            # If the query has a RETURNING clause, fetch the returned rows
            if cursor.description:
                result = cursor.fetchall()  # Fetch all returned rows if any
                connection.commit()  # Commit after insertion
                return result
            else:
                connection.commit()  # Commit if only affecting rows
                return cursor.rowcount  # Return number of affected rows
    
    except IntegrityError as e:
        print("Integrity Error: Failed to insert data due to integrity error", e)
        raise
    except Exception as e:
        print("General Error executing query:", e)
        raise



@csrf_exempt
def login_view(request):
    if request.method == "POST":
        data = json.loads(request.body)
        mobile_no = data.get("mobile_no")

         # List of required fields
        required_fields = {
            "mobile_no": mobile_no,
        }
        # Check for missing fields
         # Use the utility function to check for missing fields
        missing_fields = check_missing_fields(required_fields)
        
        # If there are missing fields, return an error response
        if missing_fields:
            return JsonResponse(
            {"message": f"Missing required fields: {', '.join(missing_fields)}"},
            status=400
        )
        
        try:
            # config = configparser.ConfigParser()
            # config.read('query_mapping.ini')
            # GET_CUSTOMER_BY_MOBILE_NUMBER = config.get('query_mapping', 'GET_CUSTOMER_BY_MOBILE_NUMBER')
            # print("GET_CUSTOMER_BY_MOBILE_NUMBER::", GET_CUSTOMER_BY_MOBILE_NUMBER)
            # Get query mappings
            query_mappings = load_query_mappings()
            
            # Use the query from mapping
            get_query = query_mappings.get('GET_CUSTOMER_BY_MOBILE_NUMBER')
            if not get_query:
                raise ValueError("Query mapping 'GET_CUSTOMER_BY_MOBILE_NUMBER' not found")
            print("get_query::", get_query)
            # get_query = """ 
            # select query from vtpartner.query_master_tbl where query_id=%s;
            # """
            get_result = select_query(get_query, ['CUST_BY_MOB'])
            
            if get_result:  
                # Construct the query by adding the WHERE clause
                query = get_result[0][0] + "=%s"
                print("query::", query)
            else:
                # Handle case when query is not found in query_master_tbl
                return JsonResponse({"message": "Query not found"}, status=404)
                
            params = [mobile_no]
            result = select_query(query, params)

            if not result:  # Changed from 'if result == []'
                try:
                    get_insert_query = """ 
                    select query from vtpartner.query_master_tbl where query_id=%s;
                    """
                    get_insert_result = select_query(get_insert_query, ['ADD_NEW_CUSTOMER_ID'])
                    
                    if get_insert_result:  
                        # Construct the query by adding the WHERE clause
                        query = get_insert_result[0][0] + "VALUES (%s) RETURNING customer_id"
                        print("query::", query)
                        values = [mobile_no]
                        new_result = insert_query(query, values)
                        print("new_result::", new_result)
                        
                        if new_result:
                            print("new_result[0][0]::", new_result[0][0])
                            customer_id = new_result[0][0]
                            response_value = [
                                {
                                    "customer_id": customer_id
                                }
                            ]
                            return JsonResponse({"result": response_value}, status=200)
                    else:
                        # Handle case when query is not found in query_master_tbl
                        return JsonResponse({"message": "Query not found"}, status=404)
                    #Insert if not found
                    # query = """
                    #     INSERT INTO vtpartner.customers_tbl (
                    #         mobile_no
                    #     ) VALUES (%s) RETURNING customer_id
                    # """
                    
                except Exception as err:
                    print("Error executing query:", err)
                    return JsonResponse({"message": "An error occurred"}, status=500)
            
            # Map the results to a list of dictionaries with meaningful keys
            response_value = [
                {
                    "customer_id": row[0],
                    "customer_name": row[1],
                    "profile_pic": row[2],
                    "is_online": row[3],
                    "ratings": row[4],
                    "mobile_no": row[5],
                    "registration_date": row[6],
                    "time": row[7],
                    "r_lat": row[8],
                    "r_lng": row[9],
                    "current_lat": row[10],
                    "current_lng": row[11],
                    "status": row[12],
                    "full_address": row[13],
                    "email": row[14],
                    "gst_no": row[15],
                    "gst_address": row[16],
                    "pincode": row[17],
                }
                for row in result
            ]
            # Return customer response
            return JsonResponse({"results": response_value}, status=200)

        except Exception as err:
            print("Error executing query:", err)
            return JsonResponse({"message": "An error occurred"}, status=500)

    return JsonResponse({"message": "Method not allowed"}, status=405)

@csrf_exempt
def update_firebase_customer_token(request):
    if request.method == "POST":
        data = json.loads(request.body)
        customer_id = data.get("customer_id")
        authToken = data.get("authToken")
        

        # List of required fields
        required_fields = {
            "customer_id": customer_id,
            "authToken": authToken,
        }
        # Check for missing fields
        missing_fields = check_missing_fields(required_fields)
        
        # If there are missing fields, return an error response
        if missing_fields:
            return JsonResponse(
                {"message": f"Missing required fields: {', '.join(missing_fields)}"},
                status=400
            )
        
        try:

            query = """
                UPDATE vtpartner.customers_tbl 
                SET 
                    authtoken = %s
                WHERE customer_id = %s
                """
            values = [
                    authToken,
                    customer_id
                ]

            # Execute the query
            row_count = update_query(query, values)

            # Send success response
            return JsonResponse({"message": f"{row_count} row(s) updated"}, status=200)

        except Exception as err:
            print("Error executing query:", err)
            return JsonResponse({"message": "An error occurred"}, status=500)

    return JsonResponse({"message": "Method not allowed"}, status=405)


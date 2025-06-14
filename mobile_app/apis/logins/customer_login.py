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
from mobile_app.apis.logins.configurations import load_query_mappings
import logging


    
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
        # print("Select_Query::=>", query)
        # print("Params::", params)
        
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            result = None
            result = cursor.fetchall()

            # if result == []:
            #     raise ValueError("No Data Found")  # Custom error when no results are found
            print("result::",result)
            return result

    except ValueError as e:
        # print(f"Error: {e}")
        raise  # Re-raise to be handled by calling function
    
    except DatabaseError as e:
        # print("DatabaseError executing query:", e)
        raise  # Re-raise to be handled by calling function

    except Exception as e:
        # print("Unexpected error:", e)
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
        try:
            data = json.loads(request.body)
            mobile_no = data.get("mobile_no")

            required_fields = {
                "mobile_no": mobile_no,
            }
            
            missing_fields = check_missing_fields(required_fields)
            if missing_fields:
                return JsonResponse(
                    {"message": f"Missing required fields: {', '.join(missing_fields)}"},
                    status=400
                )
            
            query_mappings = load_query_mappings()
            get_query = query_mappings.get('GET_CUSTOMER_BY_MOBILE_NUMBER')
            
            if not get_query:
                return JsonResponse({"message": "Query mapping not found"}, status=404)
            
            # First get the customer query
            get_result = select_query(get_query, ['CUST_BY_MOB'])
            
            if not get_result:
                return JsonResponse({"message": "Customer query not found"}, status=404)
                
            # Construct and execute the customer query
            query = get_result[0][0] + "=%s"
            result = select_query(query, [mobile_no])

            if not result:
                # Try to insert new customer
                get_insert_query = query_mappings.get('ADD_NEW_CUSTOMER_ID')
                logger = logging.getLogger('CustomerLogin')
                logger.warning("Insert query mapping:", get_insert_query)
                logger.info("Insert query mapping:", get_insert_query)
                logger.debug("Insert query mapping:", get_insert_query)
                if not get_insert_query:
                    return JsonResponse({"message": "Insert query mapping not found"}, status=404)
                
                get_insert_result = select_query(get_insert_query, ['ADD_NEW_CUSTOMER_ID'])
                if not get_insert_result:
                    return JsonResponse({"message": "Insert query not found"}, status=404)
                
                insert_query_str = get_insert_result[0][0] + "VALUES (%s) RETURNING customer_id"
                new_result = insert_query(insert_query_str, [mobile_no])
                
                if new_result:
                    customer_id = new_result[0][0]
                    return JsonResponse({
                        "result": [{
                            "customer_id": customer_id
                        }]
                    }, status=200)
                    
            # Map existing customer results
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
            
            return JsonResponse({"results": response_value}, status=200)

        except Exception as err:
            print("Error executing query:", err)
            return JsonResponse({"message": str(err)}, status=500)

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


import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from mobile_app.configurations import load_query_mappings
import logging
from mobile_app.views import select_query, insert_query, update_query, check_missing_fields
import jwt
import datetime
import logging

from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework_simplejwt.tokens import AccessToken,RefreshToken, TokenError

# Initialize logger
logger = logging.getLogger('ApplicationLogger')




@csrf_exempt
def generate_customer_jwt_token_api(request):
    if request.method != "POST":
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
        required_fields = ['customer_id', 'mobile_no', 'device_emei_no', 'api_encrpted_user_id']
        missing_fields = [field for field in required_fields if field not in data]

        if missing_fields:
            return JsonResponse({'detail': f'Missing fields: {", ".join(missing_fields)}'}, status=400)

        # FakeUser is a custom user-like object (optional) to avoid actual DB user if you don’t have one
        class FakeUser:
            def __init__(self, id):
                self.id = id
            @property
            def is_authenticated(self):
                return True

        user = FakeUser(data['customer_id'])

        refresh = RefreshToken.for_user(user)
        # Add custom claims
        refresh['mobile_no'] = data['mobile_no']
        refresh['device_emei_no'] = data['device_emei_no']
        refresh['api_encrpted_user_id'] = data['api_encrpted_user_id']

        return JsonResponse({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'expires_in': str(refresh.access_token.lifetime)
        }, status=200)

    except json.JSONDecodeError:
        return JsonResponse({'detail': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'detail': f'Error: {str(e)}'}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class ValidateCustomerTokenView(APIView):
    
    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({'detail': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            exp_timestamp = decoded['exp']
            now_timestamp = datetime.datetime.utcnow().timestamp()
            remaining_seconds = int(exp_timestamp - now_timestamp)

            return Response({
                'valid': True,
                'expires_in_seconds': remaining_seconds,
                'customer_id': decoded.get('user_id'),
                'mobile_no': decoded.get('mobile_no'),
                'device_emei_no': decoded.get('device_emei_no'),
                'api_encrpted_user_id': decoded.get('api_encrpted_user_id'),
                'exp': exp_timestamp
            })

        except ExpiredSignatureError:
            logger.warning("Token expired")
            return Response({'valid': False, 'detail': 'Token has expired'}, status=status.HTTP_401_UNAUTHORIZED)
        except InvalidTokenError as e:
            logger.error(f"Invalid token: {str(e)}")
            return Response({'valid': False, 'detail': 'Invalid token'}, status=status.HTTP_401_UNAUTHORIZED)

@method_decorator(csrf_exempt, name='dispatch')
class RefreshCustomerTokenView(APIView):

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({'detail': 'Refresh token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            refresh = RefreshToken(refresh_token)
            access = str(refresh.access_token)
            expires_in = refresh.access_token.lifetime.total_seconds()

            return Response({
                'access': access,
                'expires_in': expires_in
            })

        except TokenError as e:
            return Response({'detail': f'Invalid or expired refresh token: {str(e)}'}, status=status.HTTP_401_UNAUTHORIZED)

def is_customer_token_expired(token_string):
    try:
        token = AccessToken(token_string)
        return False, f"Token is valid. Expires at: {token['exp']}"
    except TokenError as e:
        return True, f"Invalid or expired token: {str(e)}"


@csrf_exempt
def login_view(request):
    
    if request.method == "POST":
        try:
            
            data = json.loads(request.body)
            mobile_no = data.get("mobile_no")
            logger.debug(f'Login attempt for mobile: {mobile_no}')
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

            
            # logger.setLevel(logging.INFO)
            logger.info('Processing login request')
            logger.warning("Warning Error")
            logger.info('Info Error')
            logger.critical("Critical error:")
            
            if not result:
                # Try to insert new customer
                get_insert_query = query_mappings.get('ADD_NEW_CUSTOMER_ID')
                
                # logger.debug("Insert query mapping:", get_insert_query)
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
            
            payload = {
                'user_id': user.id,
                'email': user.email,
            }
            token = jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')
            
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


import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from mobile_app.configurations import load_query_mappings
import logging
from mobile_app.views import select_query, insert_query, update_query, check_missing_fields



# Initialize logger
logger = logging.getLogger('ApplicationLogger')

@csrf_exempt
def customer_new_registration(request):
    try:
        data = json.loads(request.body)
        #customer_id,customer_name,profile_pic,is_online,ratings,mobile_no,registration_date,time,r_lat,r_lng,current_lat,current_lng,status,full_address,email
        customer_id = data.get("customer_id")
        customer_name = data.get("customer_name")
        r_lat = data.get("r_lat")
        r_lng = data.get("r_lng")
        full_address = data.get("full_address")
        purpose = data.get("purpose")
        email = data.get("email")
        pincode = data.get("pincode")
        # authToken = data.get('auth')
        
        # if not is_valid_customer_fcm_token(customer_id, authToken):
        #     return JsonResponse({"message": "Invalid or expired token. Please login again.","status":"unauthorized"}, status=401)
        
        
        
        # List of required fields
        required_fields = {
            "customer_id":customer_id,
            "customer_name":customer_name,
            "r_lat":r_lat,
            "r_lng":r_lng,
            "full_address":full_address,
            "purpose":purpose,
            "email":email,
            "pincode":pincode,
        }

        # Use the utility function to check for missing fields
        missing_fields = check_missing_fields(required_fields)

        # If there are missing fields, return an error response
        if missing_fields:
            return JsonResponse(
                {"message": f"Missing required fields: {', '.join(missing_fields)}"},
                status=400
            )

        
        query = """
            UPDATE vtpartner.customers_tbl 
            SET customer_name=%s, r_lat=%s, r_lng=%s, current_lat=%s, current_lng=%s, full_address=%s, purpose=%s, 
            email=%s, pincode=%s
            WHERE customer_id=%s
        """
        values = [
            customer_name ,
            r_lat ,
            r_lng ,
            r_lat ,
            r_lng ,
            full_address ,
            purpose ,
            email ,
            pincode ,
            customer_id
        ]
        row_count = update_query(query, values)

        # Send success response
        return JsonResponse({"message": f"{row_count} row(s) updated"}, status=200)

    except Exception as err:
        print("Error executing add new faq query", err)
        return JsonResponse({"message": "Error executing add new faq query"}, status=500)

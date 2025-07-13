import string
from decimal import Decimal
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from mobile_app.views import select_query, insert_query, update_query
import logging

from django.views.decorators.csrf import csrf_exempt
import random
import time

def generate_unique_referral_code():
    """Generate a unique 6-character alphanumeric referral code"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        # Check if code already exists
        check_query = "SELECT COUNT(*) FROM vtpartner.referral_code_tbl WHERE referral_code = %s"
        result = select_query(check_query, [code])
        
        if not result:
            # If query fails, assume code doesn't exist
            return code
        elif result[0][0] == 0:
            # Code doesn't exist, safe to use
            return code

@csrf_exempt
def generate_referral_code(request):
    """Generate or get existing referral code for a customer"""
    if request.method == "POST":
        earnings_result = None
        completed_referrals_result = None
        total_referrals_result = None
        try:
            data = json.loads(request.body)
            customer_id = data.get('customer_id')
            
            if not customer_id:
                return JsonResponse({
                    "message": "Customer ID is required",
                    "status": "error"
                }, status=400)
            
            # Check if customer already has a referral code
            existing_query = """
                SELECT referral_code 
                FROM vtpartner.referral_code_tbl 
                WHERE customer_id = %s
            """
            existing_result = select_query(existing_query, [customer_id])
            
            if existing_result:
                referral_code = existing_result[0][0]
            else:
                # Generate new referral code
                referral_code = generate_unique_referral_code()
                
                # Insert new referral code
                insert_query_text = """
                    INSERT INTO vtpartner.referral_code_tbl (customer_id, referral_code)
                    VALUES (%s, %s)
                """
                insert_query(insert_query_text, [customer_id, referral_code])
            
            # Get customer details for sharing
            customer_query = """
                SELECT customer_name, mobile_no 
                FROM vtpartner.customers_tbl 
                WHERE customer_id = %s
            """
            customer_result = select_query(customer_query, [customer_id])
            
            if not customer_result:
                customer_name = "User"
            else:
                customer_name = customer_result[0][0]
            
            # Get referral statistics - using simple queries that work with your DB
            # First get total referrals count
            total_referrals_query = """
                SELECT COUNT(*) 
                FROM vtpartner.referral_usage_tbl 
                WHERE referred_by_code = %s
            """
            total_referrals_result = select_query(total_referrals_query, [referral_code])
            
            if not total_referrals_result:
                total_referrals = 0
            else:
                total_referrals = total_referrals_result[0][0]
            
            # Get completed referrals count (count of referral bonus transactions)
            completed_referrals_query = """
                SELECT COUNT(*) 
                FROM vtpartner.customer_wallet_transactions 
                WHERE customer_id = %s 
                AND remarks LIKE '%Referral bonus%'
                AND status = 'SUCCESS'
            """
            completed_referrals_result = select_query(completed_referrals_query, [customer_id])
            
            if not completed_referrals_result and completed_referrals_result[0][0] is None:
                completed_referrals = 0
            # else:
                # completed_referrals = completed_referrals_result[0][0]
            
            # Calculate total earnings - using simple SUM query
            earnings_query = """
                SELECT SUM(amount) 
                FROM vtpartner.customer_wallet_transactions 
                WHERE customer_id = %s 
                AND remarks LIKE '%Referral bonus%'
                AND status = 'SUCCESS'
            """
            earnings_result = select_query(earnings_query, [customer_id])
            
            if not earnings_result and earnings_result[0][0] is None:
                total_earnings = 0
            # else:
                # Handle NULL from SUM when no rows match
                # amount = earnings_result[0][0]
                # total_earnings = float(amount) if amount is not None else 0
            
            return JsonResponse({
                "status": "success",
                "referral_code": referral_code,
                "customer_name": customer_name,
                "share_message": f"Join KAPS using my referral code {referral_code} and get ₹10 bonus! Experience seamless transportation and on-demand services. Download: https://play.google.com/store/apps/details?id=com.kapstranspvtltd.kaps&hl=en_IN",
                "statistics": {
                    "total_referrals": total_referrals,
                    "completed_referrals": completed_referrals,
                    "pending_referrals": total_referrals - completed_referrals,
                    "total_earnings": total_earnings
                }
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                "message": "Invalid JSON in request body",
                "status": "error"
            }, status=400)
        except Exception as err:
            print(f"earnings_result: {earnings_result}")
            print(f"completed_referrals_result: {completed_referrals_result}")
            print(f"total_referrals_result: {total_referrals_result}")
            
            print("Error in generate_referral_code:", err)
            return JsonResponse({
                "message": "Internal Server Error",
                "status": "error",
                "error": str(err)
            }, status=500)
    
    return JsonResponse({
        "message": "Method not allowed",
        "status": "error"
    }, status=405)

@csrf_exempt
def apply_referral_code(request):
    """Apply referral code when new customer signs up"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            customer_id = data.get('customer_id')
            referral_code = data.get('referral_code', '').upper().strip()
            
            if not customer_id or not referral_code:
                return JsonResponse({
                    "message": "Customer ID and referral code are required",
                    "status": "error"
                }, status=400)
            
            # Validate referral code format
            if len(referral_code) != 6 or not referral_code.isalnum():
                return JsonResponse({
                    "message": "Invalid referral code format",
                    "status": "error"
                }, status=400)
            
            # Check if customer has already used a referral code
            existing_usage_query = """
                SELECT COUNT(*) 
                FROM vtpartner.referral_usage_tbl 
                WHERE used_by_customer = %s
            """
            existing_usage = select_query(existing_usage_query, [customer_id])
            
            if not existing_usage:
                has_used_referral = False
            else:
                has_used_referral = existing_usage[0][0] > 0
                
            if has_used_referral:
                return JsonResponse({
                    "message": "You have already used a referral code",
                    "status": "error"
                }, status=400)
            
            # Check if referral code exists and get referrer details
            referrer_query = """
                SELECT rct.customer_id, ct.customer_name 
                FROM vtpartner.referral_code_tbl rct
                JOIN vtpartner.customers_tbl ct ON rct.customer_id = ct.customer_id
                WHERE rct.referral_code = %s
            """
            referrer_result = select_query(referrer_query, [referral_code])
            
            if not referrer_result:
                return JsonResponse({
                    "message": "Invalid referral code",
                    "status": "error"
                }, status=400)
            
            referrer_id = referrer_result[0][0]
            referrer_name = referrer_result[0][1]
            
            # Check if customer is trying to use their own referral code
            if referrer_id == customer_id:
                return JsonResponse({
                    "message": "You cannot use your own referral code",
                    "status": "error"
                }, status=400)
            
            # Record referral usage
            usage_insert_query = """
                INSERT INTO vtpartner.referral_usage_tbl (referred_by_code, used_by_customer)
                VALUES (%s, %s)
            """
            insert_query(usage_insert_query, [referral_code, customer_id])
            
            # Referral bonus amounts
            referee_bonus = Decimal('10.00')  # New user gets ₹10
            referrer_bonus = Decimal('10.00')  # Referrer gets ₹10
            
            current_time = time.time()
            
            # Create or update wallet for referee (new customer)
            referee_wallet_query = """
                SELECT wallet_id 
                FROM vtpartner.customer_wallet 
                WHERE customer_id = %s
            """
            referee_wallet_result = select_query(referee_wallet_query, [customer_id])
            
            if not referee_wallet_result:
                # Create new wallet for referee
                create_wallet_query = """
                    INSERT INTO vtpartner.customer_wallet (customer_id, current_balance, last_updated)
                    VALUES (%s, %s, %s)
                    RETURNING wallet_id
                """
                wallet_result = insert_query(create_wallet_query, [customer_id, referee_bonus, current_time])
                referee_wallet_id = wallet_result[0][0]
            else:
                referee_wallet_id = referee_wallet_result[0][0]
                # Update existing wallet
                update_wallet_query = """
                    UPDATE vtpartner.customer_wallet 
                    SET current_balance = current_balance + %s, last_updated = %s
                    WHERE customer_id = %s
                """
                update_query(update_wallet_query, [referee_bonus, current_time, customer_id])
            
            # Add transaction for referee
            referee_transaction_query = """
                INSERT INTO vtpartner.customer_wallet_transactions 
                (wallet_id, customer_id, transaction_type, amount, status, transaction_time, 
                 transaction_date, payment_mode, remarks)
                VALUES (%s, %s, 'CREDIT', %s, 'SUCCESS', %s, CURRENT_DATE, 'Referral', 
                        'Referral bonus for joining with code: ' || %s)
            """
            insert_query(referee_transaction_query, [
                referee_wallet_id, customer_id, referee_bonus, current_time, referral_code
            ])
            
            # Create or update wallet for referrer
            referrer_wallet_query = """
                SELECT wallet_id 
                FROM vtpartner.customer_wallet 
                WHERE customer_id = %s
            """
            referrer_wallet_result = select_query(referrer_wallet_query, [referrer_id])
            
            if not referrer_wallet_result:
                # Create new wallet for referrer
                create_wallet_query = """
                    INSERT INTO vtpartner.customer_wallet (customer_id, current_balance, last_updated)
                    VALUES (%s, %s, %s)
                    RETURNING wallet_id
                """
                wallet_result = insert_query(create_wallet_query, [referrer_id, referrer_bonus, current_time])
                referrer_wallet_id = wallet_result[0][0]
            else:
                referrer_wallet_id = referrer_wallet_result[0][0]
                # Update existing wallet
                update_wallet_query = """
                    UPDATE vtpartner.customer_wallet 
                    SET current_balance = current_balance + %s, last_updated = %s
                    WHERE customer_id = %s
                """
                update_query(update_wallet_query, [referrer_bonus, current_time, referrer_id])
            
            # Add transaction for referrer
            referrer_transaction_query = """
                INSERT INTO vtpartner.customer_wallet_transactions 
                (wallet_id, customer_id, transaction_type, amount, status, transaction_time, 
                 transaction_date, payment_mode, remarks)
                VALUES (%s, %s, 'CREDIT', %s, 'SUCCESS', %s, CURRENT_DATE, 'Referral', 
                        'Referral bonus for referring new customer')
            """
            insert_query(referrer_transaction_query, [
                referrer_wallet_id, referrer_id, referrer_bonus, current_time
            ])
            
            return JsonResponse({
                "status": "success",
                "message": f"Referral code applied successfully! You earned ₹{referee_bonus}",
                "bonus_amount": float(referee_bonus),
                "referrer_name": referrer_name,
                "referrer_bonus": float(referrer_bonus)
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                "message": "Invalid JSON in request body",
                "status": "error"
            }, status=400)
        except Exception as err:
            print("Error in apply_referral_code:", err)
            return JsonResponse({
                "message": "Internal Server Error",
                "status": "error",
                "error": str(err)
            }, status=500)
    
    return JsonResponse({
        "message": "Method not allowed",
        "status": "error"
    }, status=405)

@csrf_exempt
def get_referral_details(request):
    """Get referral statistics and history for a customer"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            customer_id = data.get('customer_id')
            
            if not customer_id:
                return JsonResponse({
                    "message": "Customer ID is required",
                    "status": "error"
                }, status=400)
            
            # Get referral code
            code_query = """
                SELECT referral_code 
                FROM vtpartner.referral_code_tbl 
                WHERE customer_id = %s
            """
            code_result = select_query(code_query, [customer_id])
            
            if not code_result:
                referral_code = None
            else:
                referral_code = code_result[0][0]
            
            if not referral_code:
                return JsonResponse({
                    "status": "success",
                    "referral_code": None,
                    "total_earnings": 0,
                    "referrals": [],
                    "statistics": {
                        "total_referrals": 0,
                        "completed_referrals": 0,
                        "pending_referrals": 0
                    }
                })
            
            # Get referral list with customer details - using simple query
            referrals_query = """
                SELECT 
                    c.customer_name,
                    ru.used_at,
                    ru.used_by_customer
                FROM vtpartner.referral_usage_tbl ru
                JOIN vtpartner.customers_tbl c ON ru.used_by_customer = c.customer_id
                WHERE ru.referred_by_code = %s
                ORDER BY ru.used_at DESC
            """
            referrals_result = select_query(referrals_query, [referral_code])
            
            referrals = []
            completed_count = 0
            
            if not referrals_result:
                # No referrals found
                pass
            else:
                for row in referrals_result:
                    
                    # For now, mark all as pending since wallet queries are causing issues
                    # We can check status individually later if needed
                    status = 'Pending'
                    
                    referral_data = {
                        "customer_name": row[0],
                        "used_at": str(row[1]),
                        "status": status,
                        "amount": 10.0 if status == 'Completed' else 0.0
                    }
                    referrals.append(referral_data)
                    if status == 'Completed':
                        completed_count += 1
            
            # Calculate total earnings - using simple SUM query
            earnings_query = """
                SELECT SUM(amount) 
                FROM vtpartner.customer_wallet_transactions 
                WHERE customer_id = %s 
                AND remarks LIKE '%Referral bonus%'
                AND status = 'SUCCESS'
            """
            earnings_result = select_query(earnings_query, [customer_id])
            
            if not earnings_result:
                total_earnings = 0
            else:
                # Handle NULL from SUM when no rows match
                amount = earnings_result[0][0]
                total_earnings = float(amount) if amount is not None else 0
            
            return JsonResponse({
                "status": "success",
                "referral_code": referral_code,
                "total_earnings": total_earnings,
                "referrals": referrals,
                "statistics": {
                    "total_referrals": len(referrals),
                    "completed_referrals": completed_count,
                    "pending_referrals": len(referrals) - completed_count
                }
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                "message": "Invalid JSON in request body",
                "status": "error"
            }, status=400)
        except Exception as err:
            print("Error in get_referral_details:", err)
            return JsonResponse({
                "message": "Internal Server Error",
                "status": "error",
                "error": str(err)
            }, status=500)
    
    return JsonResponse({
        "message": "Method not allowed",
        "status": "error"
    }, status=405)

@csrf_exempt
def validate_referral_code(request):
    """Validate if a referral code exists and is valid"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            referral_code = data.get('referral_code', '').upper().strip()
            customer_id = data.get('customer_id')  # Optional, to check self-referral
            
            if not referral_code:
                return JsonResponse({
                    "message": "Referral code is required",
                    "status": "error"
                }, status=400)
            
            if len(referral_code) != 6 or not referral_code.isalnum():
                return JsonResponse({
                    "message": "Invalid referral code format",
                    "status": "error",
                    "valid": False
                }, status=400)
            
            # Check if referral code exists
            check_query = """
                SELECT rct.customer_id, ct.customer_name 
                FROM vtpartner.referral_code_tbl rct
                JOIN vtpartner.customers_tbl ct ON rct.customer_id = ct.customer_id
                WHERE rct.referral_code = %s
            """
            result = select_query(check_query, [referral_code])
            
            if not result:
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid referral code",
                    "valid": False
                })
            
            referrer_id = result[0][0]
            referrer_name = result[0][1]
            
            # Check if customer is trying to use their own code
            if customer_id and str(referrer_id) == str(customer_id):
                return JsonResponse({
                    "status": "error",
                    "message": "You cannot use your own referral code",
                    "valid": False
                })
            
            return JsonResponse({
                "status": "success",
                "message": "Valid referral code",
                "valid": True,
                "referrer_name": referrer_name,
                "bonus_amount": 10.0
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                "message": "Invalid JSON in request body",
                "status": "error"
            }, status=400)
        except Exception as err:
            print("Error in validate_referral_code:", err)
            return JsonResponse({
                "message": "Internal Server Error",
                "status": "error",
                "error": str(err)
            }, status=500)
    
    return JsonResponse({
        "message": "Method not allowed",
        "status": "error"
    }, status=405)



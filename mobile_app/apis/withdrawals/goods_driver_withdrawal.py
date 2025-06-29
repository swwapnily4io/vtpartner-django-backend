import json
import uuid
import time
from datetime import datetime
import logging
import razorpay
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from mobile_app.configurations import load_query_mappings
from mobile_app.views import select_query, insert_query, update_query

# Configuration
RAZORPAY_KEY_ID = 'rzp_test_4Zsazxushfh47G'
RAZORPAY_KEY_SECRET = 'iRfDnoaWdOjz3hETdPuTuwJg'
RAZORPAY_ACCOUNT_NUMBER = '2323230076522200'
WEBHOOK_SECRET = 'your_webhook_secret'

# Initialize logger
logger = logging.getLogger('ApplicationLogger')

def check_missing_fields(data, required_fields):
    """
    Check for missing required fields in the data dictionary
    """
    missing = []
    for field in required_fields:
        if field not in data or data[field] is None or data[field] == "":
            missing.append(field)
    return missing

@csrf_exempt
def initiate_driver_withdrawal(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            required_fields = ['driver_id','driver_name', 'amount', 'payment_method']
            
            # Check for missing fields
            missing = check_missing_fields(data, required_fields)
            if missing:
                return JsonResponse({
                    "status": "error",
                    "message": f"Missing required fields: {', '.join(missing)}"
                }, status=400)
            
            driver_id = data.get('driver_id')
            amount = float(data.get('amount', 0))
            payment_method = data.get('payment_method')
            
            # Additional validation for payment method specific fields
            if payment_method == "BANK":
                bank_fields = ['account_number', 'ifsc_code', 'account_name']
                missing = check_missing_fields(data, bank_fields)
                if missing:
                    return JsonResponse({
                        "status": "error",
                        "message": f"Missing bank details: {', '.join(missing)}"
                    }, status=400)
            elif payment_method == "UPI":
                if not data.get('upi_id'):
                    return JsonResponse({
                        "status": "error",
                        "message": "UPI ID is required"
                    }, status=400)
            else:
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid payment method. Use 'BANK' or 'UPI'"
                }, status=400)
            
            # Log the withdrawal request
            logger.info(f"Withdrawal request: Driver {driver_id}, Amount {amount}, Method {payment_method}")
            
            # Validate amount
            if amount < 10:
                return JsonResponse({
                    "status": "error",
                    "message": "Minimum withdrawal amount is ₹10"
                }, status=400)
            
            # Check balance
            balance_query = """
                SELECT current_balance, wallet_id
                FROM vtpartner.goods_driver_wallet 
                WHERE driver_id = %s
            """
            
            result = select_query(balance_query, [driver_id])
            if not result:
                return JsonResponse({
                    "status": "error",
                    "message": "Wallet not found"
                }, status=404)
            
            current_balance = float(result[0][0])
            wallet_id = result[0][1]
            
            if amount > current_balance:
                return JsonResponse({
                    "status": "error",
                    "message": "Insufficient balance"
                }, status=400)
            
            # Create withdrawal record
            if payment_method == "BANK":
                withdraw_query = """
                    INSERT INTO vtpartner.goods_driver_withdrawals 
                    (driver_id, amount, payment_method, account_number, 
                     ifsc_code, account_name, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', extract(epoch from CURRENT_TIMESTAMP))
                    RETURNING withdrawal_id
                """
                params = [driver_id, amount, payment_method, data['account_number'], 
                        data['ifsc_code'], data['account_name']]
            else:  # UPI
                withdraw_query = """
                    INSERT INTO vtpartner.goods_driver_withdrawals 
                    (driver_id, amount, payment_method, upi_id, 
                     status, created_at)
                    VALUES (%s, %s, %s, %s, 'PENDING', extract(epoch from CURRENT_TIMESTAMP))
                    RETURNING withdrawal_id
                """
                params = [driver_id, amount, payment_method, data['upi_id']]
            
            withdrawal_id = insert_query(withdraw_query, params)[0][0]
            
            # Update wallet balance
            update_balance_query = """
                UPDATE vtpartner.goods_driver_wallet 
                SET current_balance = current_balance - %s,
                    last_updated = extract(epoch from CURRENT_TIMESTAMP)
                WHERE driver_id = %s
            """
            update_query(update_balance_query, [amount, driver_id])
            
            # Create transaction record
            transaction_query = """
                INSERT INTO vtpartner.goods_driver_wallet_transactions 
                (wallet_id, driver_id, transaction_type, amount, status,
                 transaction_time, transaction_date, reference_id, 
                 payment_mode, remarks)
                VALUES (%s, %s, %s, %s, %s, 
                        extract(epoch from CURRENT_TIMESTAMP), 
                        CURRENT_DATE, %s, %s, %s)
            """
            
            transaction_params = [
                wallet_id,
                driver_id,
                'WITHDRAWAL',
                amount,
                'PENDING',
                str(withdrawal_id),
                payment_method,
                f"Withdrawal initiated via {payment_method}"
            ]
            
            insert_query(transaction_query, transaction_params)
            
            try:
                payout_response = initiate_razorpay_payout(data)
                
                # Update withdrawal status with Razorpay reference
                update_withdrawal_query = """
                    UPDATE vtpartner.goods_driver_withdrawals 
                    SET razorpay_payout_id = %s,
                        remarks = %s,
                        payment_details = %s::jsonb
                    WHERE withdrawal_id = %s
                """
                
                # Store payment details as JSON
                payment_details = {
                    "razorpay_id": payout_response['id'],
                    "mode": payout_response['mode'],
                    "status": payout_response['status'],
                    "utr": payout_response.get('utr', ''),
                    "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                update_query(
                    update_withdrawal_query, 
                    [payout_response['id'], 
                     "Razorpay payout initiated",
                     json.dumps(payment_details),
                     withdrawal_id]
                )
                
                logger.info(f"Withdrawal initiated successfully: ID {withdrawal_id}, "
                          f"Razorpay ID {payout_response['id']}")
                
                return JsonResponse({
                    "status": "success",
                    "message": "Withdrawal initiated successfully",
                    "withdrawal_id": withdrawal_id,
                    "razorpay_payout_id": payout_response['id'],
                    "payment_details": payment_details
                })
                
            except Exception as e:
                logger.error(f"Razorpay payout failed: {str(e)}")
                rollback_driver_withdrawal(withdrawal_id, driver_id, amount, wallet_id)
                raise e
                
        except Exception as err:
            logger.error(f"Error processing withdrawal: {str(err)}")
            return JsonResponse({
                "status": "error",
                "message": "Internal Server Error",
                "error": str(err)
            }, status=500)
    
    return JsonResponse({
        "status": "error",
        "message": "Method not allowed"
    }, status=405)

def rollback_driver_withdrawal(withdrawal_id, driver_id, amount, wallet_id):
    # Update withdrawal status
    update_withdrawal_query = """
        UPDATE vtpartner.goods_driver_withdrawals 
        SET status = 'FAILED',
            remarks = 'Razorpay payout failed'
        WHERE withdrawal_id = %s
    """
    update_query(update_withdrawal_query, [withdrawal_id])
    
    # Restore wallet balance
    restore_query = """
        UPDATE vtpartner.goods_driver_wallet 
        SET current_balance = current_balance + %s,
            last_updated = extract(epoch from CURRENT_TIMESTAMP)
        WHERE driver_id = %s
    """
    update_query(restore_query, [amount, driver_id])
    
    # Create reversal transaction
    reversal_query = """
        INSERT INTO vtpartner.goods_driver_wallet_transactions 
        (wallet_id, driver_id, transaction_type, amount, status,
         transaction_time, transaction_date, reference_id, 
         payment_mode, remarks)
        VALUES (%s, %s, %s, %s, %s, 
                extract(epoch from CURRENT_TIMESTAMP), 
                CURRENT_DATE, %s, %s, %s)
    """
    
    reversal_params = [
        wallet_id,
        driver_id,
        'WITHDRAWAL_REVERSAL',
        amount,
        'COMPLETED',
        str(withdrawal_id),
        'SYSTEM',
        'Withdrawal failed - amount reversed'
    ]
    
    insert_query(reversal_query, reversal_params)

import requests
from requests.auth import HTTPBasicAuth

def initiate_razorpay_payout(data):
    try:
        reference_id = str(uuid.uuid4())
        current_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

        payout_url = "https://api.razorpay.com/v1/payouts"
        headers = {
            "Content-Type": "application/json"
        }

        # Shared base payload
        base_payload = {
            "account_number": RAZORPAY_ACCOUNT_NUMBER,
            "amount": int(float(data['amount']) * 100),  # in paise
            "currency": "INR",
            "purpose": "payout",
            "queue_if_low_balance": True,
            "reference_id": reference_id,
            "narration": "Payout to driver",
            "notes": {
                "notes_key_1": "Wallet Withdrawal",
                "notes_key_2": str(data['driver_name'])+" - " + str(data['driver_id']),
            }
        }

        if data['payment_method'] == "BANK":
            payout_data = {
                **base_payload,
                "mode": "NEFT",
                "fund_account": {
                    "account_type": "bank_account",
                    "bank_account": {
                        "name": data['account_name'],
                        "ifsc": data['ifsc_code'],
                        "account_number": data['account_number']
                    },
                    "contact": {
                        "name": data['account_name'],
                        "type": "customer",
                        "contact": data.get('contact_no', '9999999999'),
                        "reference_id": reference_id,
                        "notes": {
                            "driver_id": str(data['driver_id'])
                        }
                    }
                }
            }
        else:  # UPI
            payout_data = {
                **base_payload,
                "mode": "UPI",
                "fund_account": {
                    "account_type": "vpa",
                    "vpa": {
                        "address": data['upi_id']
                    },
                    "contact": {
                        "name": data.get('driver_name', f"Driver {data['driver_id']}"),
                        "type": "self",
                        "contact": data.get('contact_no', '9999999999'),
                        "reference_id": reference_id,
                        "notes": {
                            "driver_id": str(data['driver_id'])
                        }
                    }
                }
            }

        # Call Razorpay payout API
        response = requests.post(
            payout_url,
            auth=HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            headers=headers,
            json=payout_data
        )

        # Handle response
        if response.status_code not in [200, 201]:
            raise Exception(f"Razorpay Payout API Error: {response.status_code} - {response.text}")

        payout_response = response.json()
        payout_response.update({
            "created_at": current_time,
            "created_by": "mohammed786-svg"
        })

        logger.info(f"Razorpay payout initiated successfully: {payout_response['id']}")
        return payout_response

    except Exception as e:
        logger.error(f"Razorpay payout creation failed: {str(e)}")
        raise Exception(f"Failed to create payout: {str(e)}")

    
@csrf_exempt
def razorpay_payout_webhook(request):
    if request.method == "POST":
        try:
            webhook_signature = request.headers.get('X-Razorpay-Signature')
            webhook_body = request.body.decode('utf-8')
            
            client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
            client.utility.verify_webhook_signature(webhook_body, webhook_signature, WEBHOOK_SECRET)
            
            webhook_data = json.loads(webhook_body)
            
            if webhook_data['event'] == 'payout.processed':
                update_payout_status(webhook_data['payload']['payout']['entity'], 'COMPLETED')
            elif webhook_data['event'] == 'payout.failed':
                update_payout_status(webhook_data['payload']['payout']['entity'], 'FAILED')
            
            return JsonResponse({"status": "success"})
            
        except Exception as e:
            logger.error(f"Webhook processing error: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": str(e)
            }, status=500)
    
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

def update_payout_status(payout_data, status):
    with transaction.atomic():
        # current_epoch = 'extract(epoch from CURRENT_TIMESTAMP)'
        
        # Update withdrawal record
        query = """
            UPDATE vtpartner.goods_driver_withdrawals 
            SET status = %s,
                completed_at = extract(epoch from CURRENT_TIMESTAMP),
                remarks = %s
            WHERE razorpay_payout_id = %s
            RETURNING withdrawal_id, driver_id, amount, wallet_id
        """
        
        result = update_query(query, [
            status,
            
            f"Payout {status.lower()}",
            payout_data['id']
        ])
        
        if result:
            withdrawal_id = result[0][0]
            driver_id = result[0][1]
            amount = result[0][2]
            wallet_id = result[0][3]
            
            # Update transaction status
            transaction_query = """
                UPDATE vtpartner.goods_driver_wallet_transactions 
                SET status = %s
                WHERE reference_id = %s
                AND transaction_type = 'WITHDRAWAL'
            """
            
            update_query(transaction_query, [status, str(withdrawal_id)])
            
            # If failed, reverse the transaction
            if status == 'FAILED':
                rollback_driver_withdrawal(withdrawal_id, driver_id, amount, wallet_id)
@csrf_exempt
def get_goods_driver_payouts(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            driver_id = data.get('driver_id')
            
            if not driver_id:
                return JsonResponse({
                    "status": "error",
                    "message": "Driver ID is required"
                }, status=400)
            
            try:
                # Function to make authenticated requests to Razorpay
                def razorpay_request(url):
                    try:
                        auth = (RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
                        response = requests.get(url, auth=auth)
                        response.raise_for_status()
                        return response.json()
                    except Exception as e:
                        logger.error(f"Razorpay API error: {str(e)}")
                        return None

                # Get all withdrawals for the driver
                withdrawals_query = """
                    SELECT w.*, 
                           g.current_balance
                    FROM vtpartner.goods_driver_withdrawals w
                    LEFT JOIN vtpartner.goods_driver_wallet g ON w.driver_id = g.driver_id
                    WHERE w.driver_id = %s
                    ORDER BY w.created_at DESC
                """
                
                withdrawals = select_query(withdrawals_query, [driver_id])
                
                # First try to get all payouts
                all_payouts_url = f"https://api.razorpay.com/v1/payouts?account_number={RAZORPAY_ACCOUNT_NUMBER}"
                all_payouts_response = razorpay_request(all_payouts_url)
                
                # Create a map of payout_id to payout details
                payout_map = {}
                if all_payouts_response and 'items' in all_payouts_response:
                    payout_map = {
                        payout['id']: payout 
                        for payout in all_payouts_response['items']
                    }
                
                payouts_data = []
                current_balance = 0
                current_time = int(time.time())
                
                for withdrawal in withdrawals:
                    try:
                        payout_details = {
                            "withdrawal_id": withdrawal[0],
                            "amount": float(withdrawal[3]),
                            "payment_method": withdrawal[4],
                            "account_number": withdrawal[5],
                            "ifsc_code": withdrawal[6],
                            "account_name": withdrawal[7],
                            "upi_id": withdrawal[8],
                            "status": withdrawal[9],
                            "razorpay_payout_id": withdrawal[10],
                            "created_at": withdrawal[11],
                            "completed_at": withdrawal[12],
                            "remarks": withdrawal[13],
                            "payment_details": withdrawal[14]
                        }
                        
                        # Get current balance from first record
                        if current_balance == 0:
                            current_balance = float(withdrawal[-1]) if withdrawal[-1] else 0.0
                        
                        # If has Razorpay ID, check status
                        razorpay_id = withdrawal[10]  # razorpay_payout_id
                        if razorpay_id:
                            # Try to get from map first, if not found make individual request
                            payout = payout_map.get(razorpay_id)
                            if not payout:
                                single_payout_url = f"https://api.razorpay.com/v1/payouts/{razorpay_id}"
                                payout_response = razorpay_request(single_payout_url)
                                if payout_response:
                                    payout = payout_response
                            
                            if payout:
                                new_status = map_razorpay_status(payout['status'])
                                
                                if new_status != withdrawal[9]:  # status column
                                    try:
                                        payment_details = {
                                            "razorpay_id": payout['id'],
                                            "status": payout['status'],
                                            "internal_status": payout.get('internal_status', ''),
                                            "utr": payout.get('utr', ''),
                                            "mode": payout['mode'],
                                            "fee": payout.get('fees', 0),
                                            "tax": payout.get('tax', 0),
                                            "fund_account_id": payout['fund_account_id'],
                                            "currency": payout['currency'],
                                            "purpose": payout['purpose'],
                                            "narration": payout['narration'],
                                            "reference_id": payout['reference_id'],
                                            "notes": payout.get('notes', {}),
                                            "status_details": payout['status_details'],
                                            "error": payout.get('error', {}),
                                            "created_at": datetime.utcfromtimestamp(payout['created_at']).strftime('%Y-%m-%d %H:%M:%S'),
                                            "last_updated": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                                        }

                                        update_query("""
                                            UPDATE vtpartner.goods_driver_withdrawals
                                            SET status = %s,
                                                completed_at = %s,
                                                remarks = %s,
                                                payment_details = %s::jsonb
                                            WHERE withdrawal_id = %s
                                        """, [
                                            new_status,
                                            current_time if new_status in ['COMPLETED', 'FAILED', 'CANCELLED'] else None,
                                            payout['status_details'].get('description', ''),
                                            json.dumps(payment_details),
                                            withdrawal[0]
                                        ])
                                        
                                        payout_details['status'] = new_status
                                        payout_details['payment_details'] = payment_details
                                        
                                    except Exception as e:
                                        logger.error(f"Failed to update withdrawal status: {str(e)}")
                        
                        payouts_data.append(payout_details)
                    except Exception as e:
                        logger.error(f"Error processing withdrawal record: {str(e)}")
                
                return JsonResponse({
                    "status": "success",
                    "current_balance": current_balance,
                    "payouts": payouts_data
                })
                
            except Exception as e:
                logger.error(f"Database query error: {str(e)}")
                return JsonResponse({
                    "status": "error",
                    "message": "Failed to fetch withdrawal records"
                }, status=500)
            
        except json.JSONDecodeError as e:
            return JsonResponse({
                "status": "error",
                "message": "Invalid JSON in request body"
            }, status=400)
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": "Internal server error"
            }, status=500)
    
    return JsonResponse({
        "status": "error",
        "message": "Method not allowed"
    }, status=405)

def map_razorpay_status(status):
    status_map = {
        'processing': 'PENDING',
        'processed': 'COMPLETED',
        'reversed': 'REVERSED',
        'cancelled': 'CANCELLED',
        'failed': 'FAILED',
        'queued': 'PENDING',
        'pending': 'PENDING'
    }
    return status_map.get(status.lower(), 'PENDING')


@csrf_exempt
def transfer_coins_to_wallet(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            customer_id = data.get('customer_id')
            coins_to_transfer = data.get('coins')
            
            if not customer_id or not coins_to_transfer:
                return JsonResponse({
                    "status": "error",
                    "message": "Missing required parameters"
                }, status=400)
            
            try:
                # Get available non-expired coins
                coins_query = """
                    SELECT 
                        COALESCE(SUM(coins_earned), 0) as total_coins,
                        COALESCE(SUM(CASE 
                            WHEN expires_at > NOW() AND is_used = false 
                            THEN coins_earned 
                            ELSE 0 
                        END), 0) as valid_coins
                    FROM vtpartner.customer_coin_rewards 
                    WHERE customer_id = %s
                """
                coins_result = select_query(coins_query, [customer_id])
                
                if not coins_result:
                    return JsonResponse({
                        "status": "error",
                        "message": "No coins found"
                    }, status=400)
                
                total_coins = int(coins_result[0][0])
                valid_coins = int(coins_result[0][1])
                
                if valid_coins < 25:
                    return JsonResponse({
                        "status": "error",
                        "message": f"Minimum 25 non-expired coins required. You have {valid_coins} valid coins."
                    }, status=400)
                
                if coins_to_transfer > valid_coins:
                    return JsonResponse({
                        "status": "error",
                        "message": f"Only {valid_coins} non-expired coins available"
                    }, status=400)

                # Check if wallet exists
                wallet_query = """
                    SELECT wallet_id FROM vtpartner.customer_wallet 
                    WHERE customer_id = %s
                """
                wallet_result = select_query(wallet_query, [customer_id])
                
                if not wallet_result:
                    # Create wallet if it doesn't exist
                    wallet_insert_query = """
                        INSERT INTO vtpartner.customer_wallet 
                            (customer_id, current_balance, last_updated)
                        VALUES (%s, 0, extract(epoch from CURRENT_TIMESTAMP))
                        RETURNING wallet_id
                    """
                    wallet_result = select_query(wallet_insert_query, [customer_id])
                
                wallet_id = wallet_result[0][0]
                
                # Mark coins as used
                update_coins_query = """
                    UPDATE vtpartner.customer_coin_rewards 
                    SET is_used = true,
                        remarks = %s
                    WHERE customer_id = %s 
                    AND expires_at > NOW() 
                    AND is_used = false
                    AND coin_id IN (
                        SELECT coin_id 
                        FROM vtpartner.customer_coin_rewards 
                        WHERE customer_id = %s 
                        AND expires_at > NOW() 
                        AND is_used = false
                        ORDER BY earned_at ASC
                        LIMIT %s
                    )
                """
                update_query(update_coins_query, [
                    f"Transferred to wallet",
                    customer_id,
                    customer_id,
                    coins_to_transfer
                ])
                
                # Add amount to wallet
                update_wallet_query = """
                    UPDATE vtpartner.customer_wallet 
                    SET current_balance = current_balance + %s,
                        last_updated = extract(epoch from CURRENT_TIMESTAMP)
                    WHERE wallet_id = %s
                """
                update_query(update_wallet_query, [coins_to_transfer, wallet_id])
                
                # Record transaction
                transaction_query = """
                    INSERT INTO vtpartner.customer_wallet_transactions
                        (wallet_id, customer_id, transaction_type, amount, 
                         status, remarks, transaction_time, transaction_date)
                    VALUES (%s, %s, %s, %s, %s, %s, 
                           extract(epoch from CURRENT_TIMESTAMP), CURRENT_DATE)
                """
                transaction_params = [
                    wallet_id,
                    customer_id,
                    'CREDIT',
                    coins_to_transfer,
                    'SUCCESS',
                    f'Transferred {coins_to_transfer} coins to wallet'
                ]
                insert_query(transaction_query, transaction_params)
                
                return JsonResponse({
                    "status": "success",
                    "message": f"Successfully transferred {coins_to_transfer} coins to wallet",
                    "remaining_coins": valid_coins - coins_to_transfer
                })
                
            except Exception as e:
                logger.error(f"Database error: {str(e)}")
                return JsonResponse({
                    "status": "error",
                    "message": "Failed to process transfer"
                }, status=500)
            
        except json.JSONDecodeError:
            return JsonResponse({
                "status": "error",
                "message": "Invalid JSON data"
            }, status=400)
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": "Internal server error"
            }, status=500)
    
    return JsonResponse({
        "status": "error",
        "message": "Method not allowed"
    }, status=405)
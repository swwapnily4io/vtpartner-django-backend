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
            required_fields = ['driver_id', 'amount', 'payment_method']
            
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
            "narration": f"KAPS Driver Payout - {reference_id}",
            "notes": {
                "driver_id": str(data['driver_id']),
                "created_at": current_time,
                "created_by": "mohammed786-svg"
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
                        "type": "vendor",
                        "contact": data.get('contact_no', '9999999999'),
                        "reference_id": f"KAPS_DRIVER_{data['driver_id']}",
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
                        "name": data.get('name', f"Driver {data['driver_id']}"),
                        "type": "self",
                        "contact": data.get('contact_no', '9999999999'),
                        "reference_id": f"KAPS_DRIVER_{data['driver_id']}",
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

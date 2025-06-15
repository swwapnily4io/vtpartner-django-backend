import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from mobile_app.configurations import load_query_mappings
import logging
from mobile_app.views import select_query, insert_query, update_query, check_missing_fields


# Initialize logger
logger = logging.getLogger('ApplicationLogger')

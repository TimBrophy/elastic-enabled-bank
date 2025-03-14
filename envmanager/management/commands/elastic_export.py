import os
import googlemaps
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk
from onlinebanking.models import BankAccount, BankAccountType, AccountTransactionType, AccountTransaction, Customer, \
    CustomerAddress, Retailer, BankingProducts
from envmanager.models import ClusterDetail
from django.core.management.base import BaseCommand
import json
import re
from config import settings
from config.settings import GOOGLE_MAPS_API_KEY

index_name = getattr(settings, 'TRANSACTION_INDEX_NAME', None)
product_index_name = getattr(settings, 'PRODUCT_INDEX', None)
pipeline_name = getattr(settings, 'TRANSACTION_PIPELINE_NAME', None)
elastic_cloud_id = getattr(settings, 'elastic_cloud_id', None)
elastic_user = getattr(settings, 'elastic_user', None)
elastic_password = getattr(settings, 'elastic_password', None)

es = Elasticsearch(
    cloud_id=elastic_cloud_id,
    http_auth=(elastic_user, elastic_password),
    request_timeout=None
)


def build_product(product_id):
    payload = {}
    product_detail = BankingProducts.objects.filter(id=product_id).first()
    payload = {
        "product_name": str(product_detail.product_name),
        "description": str(product_detail.description),
        "bank_account_type": product_detail.account_type.account_type
    }
    payload = json.dumps(payload)
    return payload


def build_record(transaction_id):
    payload = {}
    transaction_details = AccountTransaction.objects.filter(id=transaction_id).first()
    bank_account_details = BankAccount.objects.filter(id=transaction_details.bank_account_id).first()
    customer_details = Customer.objects.filter(id=bank_account_details.customer_id).first()

    payload = {
        "transaction_date": transaction_details.transaction_date.strftime("%Y-%m-%d"),
        "bank_account_number": str(transaction_details.bank_account),
        "bank_account_type": str(bank_account_details.account_type),
        "transaction_category": transaction_details.transaction_category.category_name,
        "transaction_type": transaction_details.transaction_type.transaction_type,
        "opening_balance": transaction_details.opening_balance,
        "transaction_value": transaction_details.transaction_value,
        "closing_balance": transaction_details.closing_balance,
        "description": transaction_details.description,
        "customer_name": f'{customer_details.first_name} {customer_details.last_name}',
        "customer_email": customer_details.email
    }
    pattern = r"merchant: (.+?), location: (.+)$"
    match = re.search(pattern, transaction_details.description)
    if match:
        merchant = match.group(1)
        retailer_format = Retailer.objects.filter(name=merchant).first()
        payload[
            'description'] = f"{transaction_details.description} - retail category: {retailer_format.dominant_operational_format}"
        payload['merchant_name'] = match.group(1)
        payload['location'] = match.group(2)
        payload['retail_category'] = retailer_format.dominant_operational_format

        gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
        geocode_result = gmaps.geocode(payload['location'])
        location = geocode_result[0]['geometry']['location']
        latitude = location['lat']
        longitude = location['lng']
        payload['geometry'] = {
            "lat": latitude,
            "lon": longitude
        }
    payload = json.dumps(payload)
    print(payload)
    return payload


# class Command(BaseCommand):
#     help = 'Export un-exported records to Elasticsearch'
#
#     def handle(self, *args, **kwargs):
#         records_to_import = AccountTransaction.objects.filter(exported=0)
#         for r in records_to_import:
#             payload = build_record(r.id)
#             index_response = es.index(index=index_name, id=r.id, document=payload, pipeline=pipeline_name)
#             r.exported = 1
#             r.save()
#
#         banking_products_to_import = BankingProducts.objects.filter(exported=0)
#         for r in banking_products_to_import:
#             payload = build_product(r.id)
#             index_response = es.index(index=product_index_name, id=r.id, document=payload, pipeline=pipeline_name)
#             r.exported = 1
#             r.save()

class Command(BaseCommand):
    help = 'Export un-exported records to Elasticsearch'

    def handle(self, *args, **kwargs):
        banking_products_to_import = BankingProducts.objects.filter(exported=0)
        for r in banking_products_to_import:
            payload = build_product(r.id)
            index_response = es.index(index=product_index_name, id=r.id, document=payload, pipeline=pipeline_name)
            r.exported = 1
            r.save()

        while True:
            # Fetch un-exported AccountTransaction records in batches of 100
            account_transactions_to_import = AccountTransaction.objects.filter(exported=0)[:100]

            # If there are no more records left, exit the loop
            if not account_transactions_to_import:
                break

            # Prepare bulk payload for AccountTransaction records
            account_transaction_payloads = [
                {
                    '_index': index_name,
                    '_id': str(r.id),
                    '_source': build_record(r.id),
                    '_pipeline': pipeline_name
                }
                for r in account_transactions_to_import
            ]

            # Perform bulk indexing
            success, _ = bulk(es, account_transaction_payloads)

            # Update the exported flag for successfully indexed records
            if success:
                for r in account_transactions_to_import:
                    r.exported = 1
                    r.save()
                self.stdout.write(self.style.SUCCESS('Indexing completed successfully.'))
            else:
                self.stdout.write(self.style.ERROR('Indexing failed.'))

            es.indices.refresh(index=index_name)



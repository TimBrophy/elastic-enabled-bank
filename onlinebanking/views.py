import json
import googlemaps
from django.shortcuts import render
from django.http import HttpResponse
from .models import BankAccount, AccountTransaction, Customer, Retailer, BankingProducts, DemoScenarios
from .forms import AccountTransactionForm, AccountTransferForm
from elasticsearch import Elasticsearch
from langchain_community.chat_models import BedrockChat
from langchain_openai import AzureChatOpenAI
from dotenv import load_dotenv
import os
from datetime import datetime, timezone, timedelta
import tiktoken
import nltk
from nltk.tokenize import word_tokenize
import re
from config import settings
import pandas as pd
from django.db.models import Q
import math
import uuid
import boto3
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64
from io import BytesIO
import logging

load_dotenv()


from langchain.schema import (
    SystemMessage,
    HumanMessage)
from config.settings import GOOGLE_MAPS_API_KEY

customer_id = getattr(settings, 'DEMO_USER_ID', None)
index_name = getattr(settings, 'TRANSACTION_INDEX_NAME', None)
elastic_cloud_id = getattr(settings, 'elastic_cloud_id', None)
elastic_user = getattr(settings, 'elastic_user', None)
elastic_password = getattr(settings, 'elastic_password', None)
model_id = getattr(settings, 'MODEL_ID', None)
pipeline_name = getattr(settings, 'TRANSACTION_PIPELINE_NAME', None)
product_index_name = getattr(settings, 'PRODUCT_INDEX', None)
customer_support_base_index = getattr(settings, 'CUSTOMER_SUPPORT_INDEX', None)
customer_support_index = f'{customer_support_base_index}_processed'
logging_index = getattr(settings, 'LLM_AUDIT_LOG_INDEX', None)
logging_pipeline = getattr(settings, 'LLM_AUDIT_LOG_INDEX_PIPELINE_NAME', None)
llm_provider = getattr(settings, 'LLM_PROVIDER', None)
llm_temperature = 0
logger = logging.getLogger('elastic-bank')

# calculate the cost of an LLM interaction
def calculate_cost(message, type):
    provider = llm_provider
    rate_card = {
        'azure': {
            'prompt': 0.003,
            'response': 0.004
        },
        'aws': {
            'prompt': 0.008,
            'response': 0.024
        }
    }
    cost_per_1k = rate_card[provider][type]
    message_token_count = num_tokens_from_string(message, "cl100k_base")
    billable_message_tokens = message_token_count / 1000
    rounded_up_message_tokens = math.ceil(billable_message_tokens)
    message_cost = rounded_up_message_tokens * cost_per_1k
    return message_cost

def get_es_client():
    """Initialize and return an Elasticsearch client."""
    return Elasticsearch(
        cloud_id=elastic_cloud_id,
        http_auth=(elastic_user, elastic_password)
    )

def log_llm_interaction(prompt, response, sent_time, received_time, answer_type, provider, model, business_process):
    es = get_es_client()
    log_id = uuid.uuid4()
    dt_latency = received_time - sent_time
    actual_latency = dt_latency.total_seconds()
    body = {
        "@timestamp": datetime.now(tz=timezone.utc),
        "prompt": prompt,
        "response": response,
        "business_process": business_process,
        "provider": provider,
        "model": model,
        "timestamp_sent": sent_time,
        "timestamp_received": received_time,
        "prompt_cost": calculate_cost(prompt, 'prompt'),
        "response_cost": calculate_cost(response, 'response'),
        "answer_type": answer_type,
        "llm_latency": actual_latency,
        "llm_temperature": llm_temperature

    }
    response = es.index(index=logging_index, id=log_id, document=body, pipeline=logging_pipeline)
    return


def init_chat_model(provider):
    if provider == 'azure':
        chat_model = AzureChatOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            openai_api_version=os.environ["AZURE_OPENAI_API_VERSION"],
            deployment_name=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            openai_api_key=os.environ["AZURE_OPENAI_KEY"],
            temperature=llm_temperature
        )
    elif provider == 'aws':
        bedrock_client = boto3.client(service_name="bedrock-runtime", region_name=os.environ['aws_region'],
                                      aws_access_key_id=os.environ['aws_access_key'],
                                      aws_secret_access_key=os.environ['aws_secret_key'])
        chat_model = BedrockChat(
            client=bedrock_client,
            model_id=os.environ['aws_model_id'],
            streaming=True,
            model_kwargs={"temperature": 0})
    return chat_model


def chat_model_message(query, context):
    # interact with the LLM
    augmented_prompt = f"""Using only the contexts below, answer the query.
    Contexts: {context}
    Query: {query}"""
    messages = [
        SystemMessage(
            content="You are a helpful financial analyst using transaction search results to answer questions. If "
                    "you do not know the answer, simply say that the data available cannot answer the question."),
        HumanMessage(content=augmented_prompt)
    ]
    return messages


def num_tokens_from_string(string: str, encoding_name: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens


def truncate_text(text, max_tokens):
    nltk.download('punkt')
    nltk.download('punkt_tab')
    tokens = word_tokenize(text)
    trimmed_text = ' '.join(tokens[:max_tokens])
    return trimmed_text


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
            'description'] = f"{transaction_details.description} - category: {retailer_format.dominant_operational_format}"
        payload['merchant_name'] = match.group(1)
        payload['location'] = match.group(2)

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
    logger.info(f"Record payload: {payload}")
    return payload


def trim_tokens(text_to_trim):
    llm_token_limit = 10000
    tokens = text_to_trim.split()
    if len(tokens) > llm_token_limit:
        trimmed_text = ' '.join(tokens[:llm_token_limit])
    else:
        trimmed_text = text_to_trim
    return trimmed_text


# Create your views here.

def customer_support(request):
    question = ""
    answer = ""
    documents = ""
    if request.method == 'POST':
        question = request.POST.get('question')
        es = get_es_client()
        query = {
            "bool": {
                "should": [
                    {
                        "text_expansion": {
                            "ml.inference.title_expanded.predicted_value": {
                                "model_id": model_id,
                                "model_text": question,
                                "boost": 2
                            }
                        }
                    },
                    {
                        "text_expansion": {
                            "ml.inference.body_content_expanded.predicted_value": {
                                "model_id": model_id,
                                "model_text": question
                            }
                        }
                    },
                    {
                        "match": {
                            "body_content": question
                        }
                    },
                    {
                        "match": {
                            "title": question
                        }
                    }
                ]
            }
        }
        customer_support_field_list = ['title', 'body_content', '_score']
        customer_support_results = es.search(index=customer_support_index, query=query, size=20,
                                             fields=customer_support_field_list, min_score=20)
        documents = []
        # Check if there are hits
        if customer_support_results['hits']['total']['value'] > 1:
            for hit in customer_support_results['hits']['hits']:
                doc_info = {
                    "score": hit["_score"],
                    "title": hit["_source"]["title"],
                    "body_content": hit["_source"]["body_content"]
                }
                documents.append(doc_info)

        context_documents = str(documents[:15])
        context_documents = truncate_text(context_documents, 12000)
        prompt_file = 'files/customer_support_prompt.txt'
        with open(prompt_file, "r") as file:
            prompt_contents_template = file.read()
            prompt = prompt_contents_template.format(question=question, context_documents=context_documents)
            augmented_prompt = prompt

        messages = [
            SystemMessage(
                content="You are a helpful customer support agent."),
            HumanMessage(content=augmented_prompt)
        ]
        sent_time = datetime.now(tz=timezone.utc)
        chat_model = init_chat_model(llm_provider)
        answer = chat_model.invoke(messages).content
        received_time = datetime.now(tz=timezone.utc)
        log_llm_interaction(augmented_prompt, answer, sent_time, received_time, 'original', 'azure', model_id, 'customer support')
    context = {
        "question": question,
        "answer": answer,
        "supporting_results": documents
    }
    return render(request, "onlinebanking/customer_support.html", context)


def financial_analysis(request):
    demo_user = Customer.objects.filter(id=customer_id).first()
    # search elastic for user transactions and aggregate them by category
    es = get_es_client()
    # Generate category spend
    query = {
        "size": 0,
        "query": {
            "term": {
                "customer_email.keyword": demo_user.email
            }
        },
        "aggs": {
            "retail_categories": {
                "terms": {
                    "field": "retail_category.keyword"
                },
                "aggs": {
                    "total_transaction_value": {
                        "sum": {
                            "field": "transaction_value"
                        }
                    }
                }
            }
        }
    }
    field_list = ["transaction_date", "description", "transaction_value", "transaction_category", "retailer_category"]
    results = es.search(index=index_name, body=query, fields=field_list)

    categories = []
    category_names = []
    total_values = []
    for bucket in results['aggregations']['retail_categories']['buckets']:
        category = {
            'name': bucket['key'],
            'total_value': bucket['total_transaction_value']['value']
        }
        categories.append(category)
        category_names.append(bucket['key'])
        total_values.append(bucket['total_transaction_value']['value'])

    # Generate the bar chart
    plt.style.use('ggplot')
    plt.figure(figsize=(10, 6))
    plt.bar(category_names, total_values, color='#0077CC')
    # plt.title('Retail Categories Total Transaction Value', fontsize=16)
    # plt.xlabel('Retail Categories', fontsize=14)
    plt.ylabel('Total Transaction Value', fontsize=14)
    plt.xticks(rotation=45, ha='right', fontsize=12)
    plt.tight_layout()

    # Save the plot to a BytesIO buffer
    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    plt.close()
    category_chart_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    # generate the daily spend
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    daily_query = {
        "query": {
            "bool": {
                "must": {
                    "range": {
                        "transaction_date": {
                            "gte": start_date.isoformat(),
                            "lte": end_date.isoformat(),
                            "format": "strict_date_optional_time"
                        }
                    }
                },
                "filter": {
                    "term": {
                        "transaction_type.keyword": "Debit"
                    }
                }
            }
        },
        "aggs": {
            "daily_totals": {
                "date_histogram": {
                    "field": "transaction_date",
                    "calendar_interval": "day"
                },
                "aggs": {
                    "total_spent": {
                        "sum": {
                            "field": "transaction_value"
                        }
                    }
                }
            }
        },
        "size": 0  # We only want aggregations
    }

    daily_results = es.search(index=index_name, body=daily_query)

    # Extract daily totals
    dates = []
    daily_totals = []
    for bucket in daily_results['aggregations']['daily_totals']['buckets']:
        date_obj = datetime.strptime(bucket['key_as_string'], '%Y-%m-%dT%H:%M:%S.%fZ')  # Parse the date string
        formatted_date = date_obj.strftime('%a, %d-%m')  # Format as weekday, dd-mm
        dates.append(formatted_date)
        daily_totals.append(bucket['total_spent']['value'])

    # Generate the line chart for daily spending totals
    plt.style.use('ggplot')
    plt.figure(figsize=(12, 6))
    plt.plot(dates, daily_totals, marker='o', linestyle='-', color='#0077CC')
    # plt.title('Total Spent Per Day Over the Last 30 Days', fontsize=16)
    plt.xlabel('Date (Weekday, dd-mm)', fontsize=14)
    plt.ylabel('Total Spent', fontsize=14)
    plt.xticks(rotation=45, ha='right', fontsize=10)
    plt.grid(True)
    plt.tight_layout()

    # Save the line chart to a BytesIO buffer
    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    plt.close()
    daily_chart_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    context = {
        'categories': categories,
        'category_chart_image': category_chart_base64,
        'dates': dates,
        'daily_totals': daily_totals,
        'daily_chart_image': daily_chart_base64
    }

    if request.method == 'POST':
        offer_summary_dict = []
        transaction_info_list = []
        answer = "There are currently no offers in the system. Please check back soon."
        if request.POST.get('interested'):
            # get current banking product offers
            all_offers = BankingProducts.objects.all()
            demo_user = Customer.objects.filter(id=customer_id).first()
            for offer in all_offers:
                new_offer_query = {
                        "rrf": {
                            "retrievers": [
                                {
                                    "standard": {
                                        "filter": {
                                            "term": {
                                                "customer_email.keyword": demo_user.email
                                            }
                                        }
                                    }
                                },
                                {
                                    "standard": {
                                        "query": {
                                            "term": {
                                                "description": offer.description
                                            }
                                        }
                                    }
                                },
                                {
                                    "standard": {
                                        "query": {
                                            "text_expansion": {
                                                "ml.inference.description_expanded.predicted_value": {
                                                    "model_id": model_id,
                                                    "model_text": offer.description
                                                }
                                            }
                                        }
                                    }
                                }
                            ],
                            "rank_window_size": 20,
                            "rank_constant": 1
                        }
                    }

                offer_query = {
                    "bool": {
                        "should": [
                            {
                                "text_expansion": {
                                    "ml.inference.description_expanded.predicted_value": {
                                        "model_id": model_id,
                                        "model_text": offer.description,
                                    }
                                }
                            },
                            {
                                "match": {
                                    "description": {
                                        "query": offer.description,
                                        "boost": 2
                                    }

                                }
                            }
                        ],
                        "filter": {
                            "term": {
                                "customer_email.keyword": demo_user.email
                            }
                        }
                    }
                }
                offer_field_list = ["transaction_date", "description", "transaction_value", "transaction_category",
                                    "retail_category"]
                matching_transactions = es.search(index=index_name, query=offer_query,
                                                  fields=offer_field_list)
                if matching_transactions['hits']['total']['value'] > 1:
                    for hit in matching_transactions['hits']['hits']:
                        transaction_info = {
                            "offer_name": offer.product_name,
                            "offer_description": offer.description,
                            "transaction_description": hit["_source"]["description"],
                            "purchase_value": hit["_source"]["transaction_value"]
                        }
                        transaction_info_list.append(transaction_info)
                    transaction_df = pd.DataFrame(transaction_info_list)
                    offer_summary = transaction_df.groupby('offer_name').agg(
                        {'offer_description': 'first'}).reset_index()
                    offer_summary_dict = offer_summary.to_dict(orient='records')
                    demo_scenario_details = DemoScenarios.objects.get(active=True)
                    demo_scenario_dict = {
                        "My region": demo_scenario_details.user_geography,
                        "Facts about me": demo_scenario_details.custom_attributes
                    }
                    prompt_file = 'files/product_offer_prompt.txt'
                    with open(prompt_file, "r") as file:
                        prompt_contents_template = file.read()
                        prompt = prompt_contents_template.format(offer_summary=offer_summary, demo_scenario=demo_scenario_dict)
                        augmented_prompt = prompt
                    messages = [
                        SystemMessage(
                            content="You are a helpful customer support agent."),
                        HumanMessage(content=augmented_prompt)
                    ]
                    sent_time = datetime.now(tz=timezone.utc)
                    chat_model = init_chat_model(llm_provider)
                    answer = chat_model.invoke(messages).content
                    received_time = datetime.now(tz=timezone.utc)
                    log_llm_interaction(augmented_prompt, answer, sent_time, received_time, 'original', 'azure', model_id,
                                        'product offer')
                else:
                    answer = "Your financial needs are currently perfectly met by your existing suite of products. Well done!"
        else:
            answer = "You have chosen not to review your financial products."

        context = {
            'categories': categories,
            'category_chart_image': category_chart_base64,
            'dates': dates,
            'daily_totals': daily_totals,
            'daily_chart_image': daily_chart_base64,
            "transaction_list": transaction_info_list,
            'answer': answer
        }

    return render(request, "onlinebanking/financial_analysis.html", context)


def search(request):
    search_term = ""
    answer = ""
    prompt_construct = ""
    question = ""
    if request.method == 'POST':
        search_term = request.POST.get('search_term')
        if search_term is None:
            search_term = request.POST.get('question')
        logger.info(f"Record search term: {search_term}")
        demo_user = Customer.objects.filter(id=customer_id).first()
        # handle the es connection for the map and conversational search components
        es = get_es_client()
        query = {
            "bool": {
                "should": [
                    {
                        "text_expansion": {
                            "ml.inference.description_expanded.predicted_value": {
                                "model_id": model_id,
                                "model_text": search_term,
                                "boost": 10
                            }
                        }
                    },
                    {
                        "match": {
                            "description": search_term
                        }
                    }
                ],
                "filter": {
                    "term": {
                        "customer_email.keyword": demo_user.email
                    }
                }
            }
        }

        field_list = ["transaction_date", "description", "transaction_value",
                      "transaction_category", "bank_account_number", "opening_balance", "closing_balance", "_score"]
        results = es.search(index=index_name, query=query, size=20, min_score=1)
        logger.info(f"Record search results: {results}")
        response_data = [{"_score": hit["_score"], **hit["_source"]} for hit in results["hits"]["hits"]]
        transaction_results = []
        # Check if there are hits
        if "hits" in results and "total" in results["hits"]:
            total_hits = results["hits"]["total"]
            # Check if there are any hits with a value greater than 0
            if isinstance(total_hits, dict) and "value" in total_hits and total_hits["value"] > 0:
                for hit in response_data:
                    doc_data = {field: hit[field] for field in field_list if field in hit}
                    transaction_results.append(doc_data)
            question = request.POST.get('question')
            if question:
                context_documents = str(transaction_results[:20])
                context_documents = truncate_text(context_documents, 10000)

                # Phase 1
                # prompt_file = 'files/auto_generate_prompt.txt'
                # with open(prompt_file, "r") as file:
                #     prompt_contents_template = file.read()
                #     prompt = prompt_contents_template.format(question=question, context_documents=context_documents, original_search=search_term)
                #     augmented_prompt = prompt
                # messages = [
                #     SystemMessage(
                #         content="You are a helpful prompt engineer."),
                #     HumanMessage(content=augmented_prompt)
                # ]
                # sent_time = datetime.now(tz=timezone.utc)
                # chat_model = init_chat_model(llm_provider)
                # prompt_construct = chat_model(messages).content
                # received_time = datetime.now(tz=timezone.utc)
                # log_llm_interaction(augmented_prompt, prompt_construct, sent_time, received_time, 'original', llm_provider, model_id,
                #                     'transaction advice')

                # Phase 2
                prompt_file = 'files/transaction_search_prompt.txt'
                with open(prompt_file, "r") as file:
                    prompt_contents_template = file.read()
                    prompt = prompt_contents_template.format(question=question, context_documents=context_documents, original_search=search_term)
                    augmented_prompt = prompt
                messages = [
                    SystemMessage(
                        content="You are a helpful prompt engineer."),
                    HumanMessage(content=augmented_prompt)
                ]
                sent_time = datetime.now(tz=timezone.utc)
                chat_model = init_chat_model(llm_provider)
                answer = chat_model.invoke(messages).content
                received_time = datetime.now(tz=timezone.utc)
                log_llm_interaction(augmented_prompt, prompt_construct, sent_time, received_time, 'original', llm_provider, model_id,
                                    'transaction advice')
    else:
        answer = []
        transaction_results = []

    context = {
        'search_term': search_term,
        'results': transaction_results,
        'reflection': prompt_construct,
        'answer': answer,
        'question': question
    }
    return render(request, "onlinebanking/search.html", context)


def transactions(request, bank_account_id):
    if request.method == 'POST':
        keyword = request.POST.get('keyword', '')
        transaction_list = AccountTransaction.objects.filter(
            Q(bank_account=bank_account_id) &
            (Q(description__icontains=keyword) | Q(transaction_date__icontains=keyword))
        ).order_by('-transaction_date')
    else:
        transaction_list = AccountTransaction.objects.filter(bank_account=bank_account_id).order_by(
            '-transaction_date')
    context = {
        'transaction_list': transaction_list,
        'bank_account_id': bank_account_id
    }
    return render(request, "onlinebanking/transactions.html", context)


def landing(request):
    demo_user = Customer.objects.filter(id=customer_id).first()
    # handle the es connection for the map and conversational search components
    es = get_es_client()
    # handle any form posting
    if request.method == 'POST':
        payment_form = AccountTransactionForm(request.POST)
        transfer_form = AccountTransferForm(request.POST)

        if payment_form.is_valid():
            # get latest balance
            latest_transaction = AccountTransaction.objects.filter(
                bank_account=payment_form.cleaned_data['bank_account']).order_by('-timestamp').first()

            new_transaction = payment_form.save(commit=False)
            new_transaction.opening_balance = latest_transaction.closing_balance
            new_transaction.transaction_value = payment_form.cleaned_data['transaction_value']
            new_transaction.closing_balance = new_transaction.opening_balance - new_transaction.transaction_value
            new_description = f"Payment to {payment_form.cleaned_data['target_bank']} | {payment_form.cleaned_data['target_account']}. {new_transaction.description}"
            new_transaction.description = new_description
            new_transaction.transaction_date = datetime.now(tz=timezone.utc)
            new_transaction.save()

        if transfer_form.is_valid():
            # work out the closing balance for the source account and save the record
            latest_transaction_source_account = AccountTransaction.objects.filter(
                bank_account=payment_form.cleaned_data['bank_account']).order_by('-timestamp').first()

            new_outbound_transfer = transfer_form.save(commit=False)
            new_outbound_transfer.opening_balance = latest_transaction_source_account.closing_balance
            new_outbound_transfer.closing_balance = new_outbound_transfer.opening_balance - new_outbound_transfer.transaction_value
            new_outbound_description = f"Outbound transfer to {transfer_form.cleaned_data['target_account']}. {transfer_form.cleaned_data['description']}"
            new_outbound_transfer.description = new_outbound_description
            new_outbound_transfer.transaction_date = datetime.now(tz=timezone.utc)

            new_outbound_transfer.save()

            # work out the closing balance for the target account and save the record
            new_inbound_transfer = AccountTransaction()
            latest_transaction_target_account = AccountTransaction.objects.filter(
                bank_account=transfer_form.cleaned_data['target_account']).order_by('-timestamp').first()
            new_inbound_transfer.transaction_value = transfer_form.cleaned_data['transaction_value']
            new_inbound_transfer.opening_balance = latest_transaction_target_account.closing_balance
            new_inbound_transfer.closing_balance = new_inbound_transfer.opening_balance + new_inbound_transfer.transaction_value
            new_inbound_description = f"Inbound transfer from {new_outbound_transfer.bank_account}. {transfer_form.cleaned_data['description']} "
            new_inbound_transfer.description = new_inbound_description
            new_inbound_transfer.bank_account = transfer_form.cleaned_data['target_account']
            new_inbound_transfer.transaction_type = transfer_form.cleaned_data['transaction_type']
            new_inbound_transfer.transaction_category = transfer_form.cleaned_data['transaction_category']
            new_inbound_transfer.transaction_date = datetime.now(tz=timezone.utc)
            new_inbound_transfer.save()

        records_to_import = AccountTransaction.objects.filter(exported=0)
        for r in records_to_import:
            payload = build_record(r.id)
            index_response = es.index(index=index_name, id=r.id, document=payload, pipeline=pipeline_name)
            r.exported = 1
            r.save()
    payment_form = AccountTransactionForm()
    transfer_form = AccountTransferForm()
    account_list = BankAccount.objects.filter(customer=customer_id)
    account_dict_list = []
    for a in account_list:
        latest_transaction = AccountTransaction.objects.filter(bank_account=a).order_by(
            '-transaction_date').first()
        if latest_transaction:
            account_dict = {
                'id': a.id,
                'account_number': a.account_number,
                'latest_balance': latest_transaction.closing_balance
            }
            account_dict_list.append(account_dict)
    context = {
        'account_dict_list': account_dict_list,
        'payment_form': payment_form,
        'transfer_form': transfer_form,
    }
    return render(request, "onlinebanking/index.html", context)

import json
import time
from django.http import StreamingHttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from onlinebanking.models import BankAccount, BankAccountType, AccountTransactionType, AccountTransaction, Customer, \
    CustomerAddress, Retailer, DemoScenarios, BankingProducts
from django.core.management import call_command
from elasticsearch import Elasticsearch, ConnectionError, AuthenticationException, exceptions
from elasticsearch.exceptions import ApiError
from elasticsearch.helpers import scan
import subprocess
from config import settings
from langchain_text_splitters import CharacterTextSplitter, TokenTextSplitter
import uuid
import os
import boto3
from langchain_community.chat_models import BedrockChat
from langchain_openai import AzureChatOpenAI
import eland as ed
from eland.ml import MLModel

customer_id = getattr(settings, 'DEMO_USER_ID', None)
index_name = getattr(settings, 'TRANSACTION_INDEX_NAME', None)
product_index_name = getattr(settings, 'PRODUCT_INDEX', None)
llm_audit_index_name = getattr(settings, 'LLM_AUDIT_LOG_INDEX', None)
llm_audit_log_pipeline_name = getattr(settings, 'LLM_AUDIT_LOG_INDEX_PIPELINE_NAME', None)
pipeline_name = getattr(settings, 'TRANSACTION_PIPELINE_NAME', None)
kb_pipeline_name = getattr(settings, 'KNOWLEDGE_BASE_PIPELINE_NAME', None)
elastic_cloud_id = getattr(settings, 'elastic_cloud_id', None)
elastic_user = getattr(settings, 'elastic_user', None)
elastic_password = getattr(settings, 'elastic_password', None)
kibana_url = getattr(settings, 'kibana_url', None)
model_id = os.environ["TRANSFORMER_MODEL"]

customer_support_base_index = getattr(settings, 'CUSTOMER_SUPPORT_INDEX', None)
customer_support_index = f'{customer_support_base_index}_processed'
llm_provider = getattr(settings, 'LLM_PROVIDER', None)
llm_temperature = 0


def get_es_client():
    try:
        client = Elasticsearch(
            cloud_id=elastic_cloud_id,
            http_auth=(elastic_user, elastic_password)
        )
        
        # Check if the client is connected
        if client.ping():
            print("Successfully connected to Elasticsearch.")
        else:
            print("Warning: Elasticsearch client is initialized but not responding.")
        
        return client
    
    except AuthenticationException:
        print("Error: Authentication failed. Please check your credentials.")
        return None
    except ConnectionError:
        print("Error: Unable to connect to Elasticsearch. Check network settings.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

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
            model_kwargs={"temperature": llm_temperature})
    return chat_model
    
# Create your views here.
def manager(request):
    es = get_es_client()

    index_exists = False
    product_index_exists = False
    llm_index_exists = False
    customer_support_base_index_exists = False
    customer_support_index_exists = False

    if es.indices.exists(index=index_name):
        index_exists = True
    if es.indices.exists(index=product_index_name):
        product_index_exists = True
    if es.indices.exists(index=llm_audit_index_name):
        llm_index_exists = True

    if es.indices.exists(index=customer_support_base_index):
        customer_support_base_index_exists = True
    if es.indices.exists(index=customer_support_index):
        customer_support_index_exists = True

    if index_exists and product_index_exists and llm_index_exists:
        indicies = "Complete"
    else:
        indicies = "Incomplete"

    if customer_support_base_index_exists and customer_support_index_exists:
        knowledge_base_status = 'Complete'
    elif customer_support_base_index_exists and not customer_support_index_exists:
        knowledge_base_status = 'Please process the base index'
    else:
        knowledge_base_status = 'Please set up a base customer support index and specify it in the .env file'

    log_pipeline_exists = es.ingest.get_pipeline(id=llm_audit_log_pipeline_name, ignore=[404])
    pipeline_exists = es.ingest.get_pipeline(id=pipeline_name, ignore=[404])

    if log_pipeline_exists and pipeline_exists:
        pipelines = "Complete"
    else:
        pipelines = "Incomplete"

    chat_model = init_chat_model(llm_provider)
    if chat_model:
        llm_status = 'Connected'
    else:
        llm_status = 'Incomplete'

    query = {
        "match_all": {}
    }
    if index_exists:
        es_record_count = es.count(index=index_name, query=query)
    else:
        es_record_count = {'count': 0}
    bank_account_count = BankAccount.objects.count()
    customer_count = Customer.objects.exclude(id=customer_id).count()
    customer_address_count = CustomerAddress.objects.count()
    account_transactions_count = AccountTransaction.objects.count()
    retailer_count = Retailer.objects.count()
    banking_product_count = BankingProducts.objects.count()

    context = {
        'es': es.info,
        'ping': es.ping,
        'view_name': 'home',
        'index_status': indicies,
        'pipeline_status': pipelines,
        'knowledge_base_status': knowledge_base_status,
        'llm_provider': llm_provider,
        'llm_status': llm_status,
        'bank_account_count': bank_account_count,
        'customer_count': customer_count,
        'customer_address_count': customer_address_count,
        'account_transactions_count': account_transactions_count,
        'retailer_count': retailer_count,
        'banking_product_count': banking_product_count,
        'es_record_count': es_record_count['count']
    }
    return render(request, 'envmanager/index.html', context=context)


def demo_scenarios(request, action=None, demo_scenario_id=None):
    if request.method == 'POST':
        id_to_update = request.POST.get('demo_scenario_id')
        if id_to_update:
            scenario_to_edit = DemoScenarios.objects.get(id=id_to_update)
            scenario_to_edit.product_name = request.POST.get('scenario_name')
            scenario_to_edit.custom_attributes = request.POST.get('custom_attributes')
            scenario_to_edit.user_geography = request.POST.get('user_geography')
            scenario_to_edit.save()
        else:
            DemoScenarios.objects.create(
                scenario_name=request.POST.get('scenario_name'),
                custom_attributes=request.POST.get('custom_attributes'),
                user_geography=request.POST.get('user_geography')
            )
    scenario_to_edit = []
    if action == 'delete':
        scenario_to_delete = DemoScenarios.objects.get(id=demo_scenario_id)
        scenario_to_delete.delete()
    elif action == 'edit':
        scenario_to_edit = DemoScenarios.objects.get(id=demo_scenario_id)
    elif action == 'activate':
        DemoScenarios.objects.all().update(active=False)
        scenario_to_activate = DemoScenarios.objects.get(id=demo_scenario_id)
        scenario_to_activate.active = True
        scenario_to_activate.save()
    demo_scenario_list = DemoScenarios.objects.all()
    demo_scenario_dict_list = []
    for ds in demo_scenario_list:
        demo_scenario_dict = {
            "demo_scenario_id": ds.id,
            "scenario_name": ds.scenario_name,
            "user_geography": ds.user_geography,
            "custom_attributes": ds.custom_attributes,
            "active": ds.active
        }
        demo_scenario_dict_list.append(demo_scenario_dict)
    context = {
        "demo_scenario_dict_list": demo_scenario_dict_list,
        "scenario_to_edit": scenario_to_edit
    }
    return render(request, 'envmanager/demo_scenarios.html', context=context)


def banking_products(request, action=None, banking_product_id=None):
    if request.method == 'POST':
        id_to_update = request.POST.get('bank_offer_id')
        if id_to_update:
            product_to_edit = BankingProducts.objects.get(id=id_to_update)
            product_to_edit.product_name = request.POST.get('product_name')
            product_to_edit.description = request.POST.get('description')
            product_to_edit.generator_keywords = request.POST.get('generator_keywords')
            product_to_edit.account_type_id = request.POST.get('account_type')
            product_to_edit.exported = 0
            product_to_edit.save()
        else:
            BankingProducts.objects.create(
                product_name=request.POST.get('product_name'),
                description=request.POST.get('description'),
                generator_keywords=request.POST.get('generator_keywords'),
                account_type_id=request.POST.get('account_type')
            )
        return redirect('banking_products')
    account_types = BankAccountType.objects.all()
    product_to_edit = []
    if action == 'delete':
        product_to_delete = BankingProducts.objects.get(id=banking_product_id)
        product_to_delete.delete()
    elif action == 'edit':
        args = [
            arg for arg in [banking_product_id]
            if arg is not None and arg != ''
        ]
        product_to_edit = BankingProducts.objects.get(id=banking_product_id)
    elif action == 'generate':
        args = [
            arg for arg in [banking_product_id]
            if arg is not None and arg != ''
        ]
        call_command('generate_scenario_data', *args)
        call_command('elastic_export')
    banking_products_list = BankingProducts.objects.all()
    banking_products_dict_list = []
    for bp in banking_products_list:
        banking_product_dict = {
            'product_name': bp.product_name,
            'banking_product_id': bp.id,
            'description': bp.description,
            'keywords': bp.generator_keywords,
            'account': bp.account_type.account_type
        }
        banking_products_dict_list.append(banking_product_dict)
    context = {
        'banking_product_dict_list': banking_products_dict_list,
        'account_types': account_types,
        'product_to_edit': product_to_edit
    }
    return render(request, 'envmanager/banking_products.html', context=context)


def cluster(request):
    print(f"elastic cloud id: {elastic_cloud_id}")

    es = get_es_client()

    context = {
        'es': es.info,
        'ping': es.ping

    }
    return render(request, 'envmanager/cluster.html', context)


def generate_data(request):
    context = {
        'view_name': 'generate_data'
    }
    return render(request, 'envmanager/index.html', context)


def execute_backend_command(request):
    if request.POST.get('command_name') == 'generate_data':
        message = 'The data generation command has been called and will execute asynchronously in the background.'
        number_of_customers = request.POST.get('number_of_customers')
        number_of_months = request.POST.get('number_of_months')
        transaction_minimum = request.POST.get('transaction_minimum')
        transaction_maximum = request.POST.get('transaction_maximum')
        # Check if arguments are not None or empty before calling call_command
        args = [
            arg for arg in [number_of_customers, number_of_months, transaction_minimum, transaction_maximum]
            if arg is not None and arg != ''
        ]
        call_command('generate_dataset', *args)
        context = {
            'view_name': 'generate_data'
        }
    return render(request, 'envmanager/command_handler.html', context)


def process_data_action(request):
    if request.method == 'POST':
        context = {
            'view_name': 'create',
            'form_data': {
                'number_of_customers': request.POST.get('number_of_customers'),
                'number_of_months': request.POST.get('number_of_months'),
                'transaction_minimum': request.POST.get('transaction_minimum'),
                'transaction_maximum': request.POST.get('transaction_maximum'),
            }
        }
    return render(request, 'envmanager/action.html', context)


def clear_data(request):
    es = get_es_client()

    query = {
        "match_all": {}
    }
    if request.method == 'POST':
        if request.POST.get('delete'):
            es.delete_by_query(index=index_name, query=query)
            Customer.objects.exclude(id=customer_id).delete()
            CustomerAddress.objects.all().delete()
            BankAccount.objects.all().delete()
            Retailer.objects.all().delete()

    es_record_count = es.count(index=index_name, query=query)
    bank_account_count = BankAccount.objects.count()
    customer_count = Customer.objects.exclude(id=customer_id).count()
    customer_address_count = CustomerAddress.objects.count()
    account_transactions_count = AccountTransaction.objects.count()
    retailer_count = Retailer.objects.count()

    context = {
        'view_name': 'clear_data',
        'bank_account_count': bank_account_count,
        'customer_count': customer_count,
        'customer_address_count': customer_address_count,
        'account_transactions_count': account_transactions_count,
        'retailer_count': retailer_count,
        'es_record_count': es_record_count['count']
    }
    return render(request, 'envmanager/index.html', context)


def run_command():
    # Use subprocess.Popen to run the command and capture output in real-time
    process = subprocess.Popen(
        ['python', 'manage.py', 'elastic_export'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # Use line buffering
        universal_newlines=True,
    )

    # Iterate over the command's output in real-time
    for line in iter(process.stdout.readline, ''):
        yield line

    # Ensure the process has completed
    process.stdout.close()
    process.wait()


def read_json_file(file_path):
    with open(file_path, 'r') as file:
        data = json.load(file)
    return data


def export_data(request):
    if request.POST.get('command_name') == 'elastic_export':
        # Create a StreamingHttpResponse with the generator function
        response = StreamingHttpResponse(run_command(), content_type="text/plain")

    else:
        response = 0

    account_transactions_count = AccountTransaction.objects.filter(exported=0).count()
    context = {
        'record_count': account_transactions_count,
        'view_name': 'export',
        'streaming_content': response
    }
    return render(request, 'envmanager/export.html', context)

def deploy_elser(model_id, request):
    """Deploys an ELSER model and returns logs to the frontend."""
    
    es = get_es_client()
    deployment_status = ""
    try:            
        # Check if the model is already downloaded
        try:
            status = es.ml.get_trained_models(model_id=model_id, include="definition_status")
            print(f"DEBUG: Model status response: {status}")
            model_exists = status.get("trained_model_configs", [{}])[0].get("fully_defined", False)

        except ApiError as e:
            # Check if it's a 404 error (model not found)
            if e.status_code == 404:
                print(f"DEBUG: Model {model_id} not found. Proceeding with model creation.")
                model_exists = False  # Indicate that model does not exist
            else:
                print(f"ERROR: Elasticsearch API Error while fetching model status: {str(e)}")
                deployment_status += f"Elasticsearch API Error while checking existing model status: {str(e)}<br>"
                raise  # Stop execution for other API errors

        if model_exists:
            deployment_status += f"{model_id} model is already downloaded and ready to be deployed.<br>"
        else:
            try:
                # Create new model if not found
                es.ml.put_trained_model(
                    model_id=model_id,
                    input={"field_names": ["concatenated_text"]}
                )
                deployment_status += f"{model_id} model creation initiated. Downloading...<br>"
            except ApiError as e:
                deployment_status += f"Elasticsearch API Error while creating model: {str(e)}<br>"
            except Exception as e:
                deployment_status += f"Unexpected error while creating model: {str(e)}<br>"
            try:
                # Check model download status
                while True:
                    status = es.ml.get_trained_models(model_id=model_id, include="definition_status")
                    if status.get("trained_model_configs", [{}])[0].get("fully_defined", False):
                        deployment_status += f"{model_id} model is downloaded and ready to be deployed.<br>"
                        break
                    else:
                        deployment_status += f"{model_id} model is downloading and not ready yet.<br>"
                    time.sleep(5)
            except ApiError as e:
                deployment_status += f"Elasticsearch API Error while checking model status: {str(e)}<br>"
            except Exception as e:
                deployment_status += f"Unexpected error while checking model status: {str(e)}<br>"
        try:
            # Start the model deployment if not already started
            if get_model_routing_state(model_id) == "started":
                print(deployment_status)
                deployment_status += f"{model_id} model has been already deployed and is currently started.<br>"
            else:
                deployment_status += f"{model_id} model will be started.<br>"
                es.ml.start_trained_model_deployment(
                    model_id=model_id,
                    number_of_allocations=1,
                    threads_per_allocation=1,
                    priority="low",
                    wait_for="started"
                )

                while True:
                    if get_model_routing_state(model_id) == "started":
                        deployment_status += f"{model_id} model has been successfully started.<br>"
                        break
                    else:
                        deployment_status += f"{model_id} model is currently being started.<br>"
                    time.sleep(5)
        except ApiError as e:
            deployment_status += f"Elasticsearch API Error while checking model status: {str(e)}<br>"
        except Exception as e:
            deployment_status += f"Unexpected error while checking model status: {str(e)}<br>"
    except ApiError as e:
        deployment_status += f"Elasticsearch API Error while starting model: {str(e)}<br>"
    except Exception as e:
        deployment_status += f"Unexpected error while starting model: {str(e)}<br>"


    return render(request, "envmanager/knowledge_base.html", {"message": deployment_status})

def get_model_routing_state(model_id):
    """Fetches the model routing state from Elasticsearch."""

    es = get_es_client()

    try:
        status = es.ml.get_trained_models_stats(model_id=model_id)
        print(f"Model stats response: {status}")  # Debugging: Print the full response

        if "trained_model_stats" in status and len(status["trained_model_stats"]) > 0:
            model_stats = status["trained_model_stats"][0]

            # Check if deployment_stats exists
            if "deployment_stats" in model_stats and "nodes" in model_stats["deployment_stats"]:
                if len(model_stats["deployment_stats"]["nodes"]) > 0:
                    routing_state = model_stats["deployment_stats"]["nodes"][0]["routing_state"]["routing_state"]
                    print(f"Model {model_id} routing state: {routing_state}")
                    return routing_state
                else:
                    print(f"Model {model_id} has no nodes deployed.")
                    return "not_deployed"

            else:
                print(f"Model {model_id} is not deployed yet (no deployment_stats).")
                return "not_deployed"

        else:
            print(f"Model {model_id} not found in trained_model_stats.")
            return "not_found"

    except ApiError as e:
        deployment_status += f"Elasticsearch API Error while fetching model routing state: {str(e)}"
        print(f"Elasticsearch API Error while fetching model routing state: {str(e)}")
        return "error"

    except Exception as e:
        deployment_status += f"Unexpected error while fetching model routing state: {str(e)}"
        print(f"Unexpected error while fetching model routing state: {str(e)}")
        return "error"

def knowledge_base(request):
    processed_kb_index = customer_support_index
    if request.POST.get('command_name') == 'execute':

        kb_pipeline_processors = read_json_file(f'files/knowledge_base_pipeline.json')
        kb_index_mapping = read_json_file(f'files/knowledge_base_mapping.json')
        kb_index_settings = read_json_file(f'files/knowledge_base_settings.json')
        # Make sure ELSER is deployed
        deploy_elser(model_id, request)

        # destroy any existing assets
        es = get_es_client()
        index_exists = es.indices.exists(index=processed_kb_index)
        if index_exists:
            print("delete index")
            es.indices.delete(index=processed_kb_index)

        pipeline_exists = es.ingest.get_pipeline(id=kb_pipeline_name, ignore=[404])
        if pipeline_exists:
            es.ingest.delete_pipeline(id=kb_pipeline_name)

        # rebuild them
        es.ingest.put_pipeline(id=kb_pipeline_name, processors=kb_pipeline_processors)
        es.indices.create(index=processed_kb_index, mappings=kb_index_mapping, settings=kb_index_settings)
        results = es.search(
            index=customer_support_base_index,
            query={"match_all": {}}, size=1000)  # Retrieve the source of the documents

        text_splitter = TokenTextSplitter(chunk_size=600, chunk_overlap=60)

        for hit in results['hits']['hits']:
            title = hit['_source']['title']
            body_content = hit['_source']['body_content']
            passages = text_splitter.split_text(body_content)
            passage_position = 1
            for i, chunked_text in enumerate(passages):
                words = chunked_text.split()
                total_words = len(words)
                print(title)
                print(passage_position)
                print(chunked_text)
                print("----------------------------------------------------")
                if total_words > 0:
                    doc_id = uuid.uuid4()
                    doc = {
                        "body_content": chunked_text,
                        "title": title,
                        "passage": passage_position,
                        "_extract_binary_content": True,
                        "_reduce_whitespace": True,
                        "_run_ml_inference": True
                    }
                    response = es.index(index=processed_kb_index, id=doc_id, document=doc, pipeline=kb_pipeline_name)
                passage_position = passage_position + 1
    context = {
        'knowledge_base': customer_support_base_index,
        'processed_kb_index': processed_kb_index,
    }
    return render(request, 'envmanager/knowledge_base.html', context)

def eland_action(request):
    """Handle form submission to deploy the Hugging Face model using Eland's CLI script."""
    if request.method == "POST" and request.POST.get("command_name") == "eland_execute":
        es = get_es_client()
        model_id = "nlptown/bert-base-multilingual-uncased-sentiment"
        es_model_id = model_id.replace("/", "__")  # Convert model ID for Elasticsearch compatibility

        try:
            # Fetch existing models
            existing_models = es.ml.get_trained_models()
            model_names = [model["model_id"] for model in existing_models.get("trained_model_configs", [])]
            if es_model_id in model_names:
                print("Model is already deployed. Skipping import.")
                deployment_status = f"Model {model_id} is already deployed in Elasticsearch."
            else:
                print("Model not found. Proceeding with import...")
                command = [
                    "eland_import_hub_model",
                    "--cloud-id", elastic_cloud_id,
                    "-u", elastic_user,
                    "-p", elastic_password,
                    "--hub-model-id", model_id,
                    "--task-type", "text_classification",
                    "--es-model-id", es_model_id,
                    "--start"
                ]
                print(f"Executing command: {' '.join(command)}")  # Debugging

                result = subprocess.run(
                    command, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL
                )
                print("Model import completed.")
                deployment_status = f"Model imported successfully! Output:\n{result.stdout}"
        except subprocess.CalledProcessError as e:
            print(f"Model import failed: {e.stderr}")
            deployment_status = f"Model import failed! Error:\n{e.stderr}"
        except Exception as e:
            print(f"Error checking model status: {e}")
            deployment_status = f"Error checking model status: {str(e)}"

        return render(request, "envmanager/knowledge_base.html", {"message": deployment_status})

def index_setup(request):
    es = get_es_client()
    if request.method == 'POST':
        transaction_index_mapping = read_json_file(f'files/transaction_index_mapping.json')
        transaction_index_settings = read_json_file(f'files/transaction_index_settings.json')
        product_index_mapping = read_json_file(f'files/product_index_mapping.json')
        product_index_settings = read_json_file(f'files/product_index_settings.json')
        llm_audit_index_mapping = read_json_file(f'files/llm_audit_log_mapping.json')
        pipeline_processors = read_json_file(f'files/transaction_index_pipeline.json')
        llm_audit_log_pipeline = read_json_file(f'files/llm_audit_log_pipeline.json')

        # destroy indices
        index_exists = es.indices.exists(index=index_name)
        if index_exists:
            es.indices.delete(index=index_name)
        product_index_exists = es.indices.exists(index=product_index_name)
        if product_index_exists:
            es.indices.delete(index=product_index_name)
        llm_index_exists = es.indices.exists(index=llm_audit_index_name)
        if llm_index_exists:
            es.indices.delete(index=llm_audit_index_name)

        # destroy pipeline
        pipeline_exists = es.ingest.get_pipeline(id=pipeline_name, ignore=[404])
        if pipeline_exists:
            es.ingest.delete_pipeline(id=pipeline_name)

        log_pipeline_exists = es.ingest.get_pipeline(id=llm_audit_log_pipeline_name, ignore=[404])
        if pipeline_exists:
            es.ingest.delete_pipeline(id=llm_audit_log_pipeline_name)

        # rebuild it all
        es.ingest.put_pipeline(id=pipeline_name, processors=pipeline_processors)
        es.ingest.put_pipeline(id=llm_audit_log_pipeline_name, processors=llm_audit_log_pipeline)
        es.indices.create(index=index_name, mappings=transaction_index_mapping, settings=transaction_index_settings)
        es.indices.create(index=product_index_name, mappings=product_index_mapping, settings=product_index_settings)
        es.indices.create(index=llm_audit_index_name, mappings=llm_audit_index_mapping)

        context = {
            'view_name': 'created'
        }
    else:
        # check if the indices exist
        index_exists = es.indices.exists(index=index_name)
        product_index_exists = es.indices.exists(index=product_index_name)
        llm_index_exists = es.indices.exists(index=llm_audit_index_name)
        pipeline_exists = es.ingest.get_pipeline(id=pipeline_name, ignore=[404])
        llm_pipeline_exists = es.ingest.get_pipeline(id=llm_audit_log_pipeline_name, ignore=[404])
        if index_exists and pipeline_exists and product_index_exists and llm_index_exists and llm_pipeline_exists:
            transaction_mapping = es.indices.get_mapping(index=index_name)
            product_mapping = es.indices.get_mapping(index=product_index_name)
            ll_audit_mapping = es.indices.get_mapping(index=llm_audit_index_name)
            pipeline = es.ingest.get_pipeline(id=pipeline_name)
            llm_audit_log_pipeline = es.ingest.get_pipeline(id=llm_audit_log_pipeline_name)
            context = {
                'view_name': 'confirmation',
                'transaction_mapping': transaction_mapping,
                'product_mapping': product_mapping,
                'llm_audit_mapping': ll_audit_mapping,
                'pipeline': pipeline,
                'llm_audit_log_pipeline': llm_audit_log_pipeline
            }
        elif ((product_index_exists and index_exists) and not pipeline_exists) or (
                pipeline_exists and not (index_exists and product_index_exists)):
            context = {
                'view_name': 'incomplete'
            }
        else:
            context = {
                'view_name': 'scratch_build'
            }
    # if the index does not exist, go ahead and build a new one
    return render(request, 'envmanager/indices.html', context)

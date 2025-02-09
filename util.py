from openai import OpenAI
import threading
import os
import re
import json

config_list = [
    {"api_key": os.environ["DEEPSEEK_API_KEY"], "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    {"api_key": os.environ['BAILIAN_API_KEY'], "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "deepseek-v3"}
]

class APIWrapper:
    def __init__(self, api_key, base_url, model):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
    
    def create(self, *args, **kwargs):
        print(f"you are using {self.model}")
        kwargs.pop('model', None)
        return self.client.chat.completions.create(model=self.model, *args, **kwargs)
         

class CompletionsWrapper:
    def __init__(self, config_list):
        self.client_list = [
            APIWrapper(**config)
            for config in config_list
        ]
        self.client_num = len(self.client_list)
        self.visit_num = 0
        self.lock = threading.Lock()
    
    def create(self, *args, **kwargs):
        with self.lock:
            current_client_index = self.visit_num % self.client_num
            self.visit_num += 1
        return self.client_list[current_client_index].create(*args, **kwargs)

class ChatWrapper:
    def __init__(self, config_list):
        self.completions = CompletionsWrapper(config_list)
        self.client_num = self.completions.client_num

class ClientWrapper:
    def __init__(self, config_list, workers_per_api=1):
        self.chat = ChatWrapper(config_list)
        self.max_workers = self.chat.client_num * workers_per_api

client = ClientWrapper(config_list)

def extract_from_code_block(text):
    matches = re.findall(r'```(.*?)```', text, re.DOTALL)
    if matches:
        return [match.strip() for match in matches]
    else:
        print("No code blocks found")
        return []


reformat_json_prompt = '''Please convert invalid input json to valid json.
The output should be presented within a code block in the following format: "json\n<output>", where "<output>" is the placeholder for the output.
'''
def reformat_json(text):
    global reformat_json_prompt, client
    completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {'role': 'system', 'content': reformat_json_prompt},
                {'role': 'user', 'content': f'```input json\n{text}```'}
            ],
            stream=False,
            temperature=0.01
        )
    
    result = completion.choices[0].message.content
    new_result = extract_from_code_block(result)[0].strip("json\n").strip("<").strip(">")
    return json.loads(new_result)

def reformat_json_multi_round(text, num_round=3):
    current_round = 0
    while current_round < num_round:
        try:
            result = reformat_json(text)
            return result
        except Exception as e:
            print(f"{current_round} failed", e)
        current_round += 1

def extract_json_from_str(str):
    result_str = str.strip("json\n").strip("<").strip(">")
    try:
        result_json = json.loads(result_str)
    except Exception as e:
        print(f"Exception: {e}")
        result_json = reformat_json_multi_round(result_str)
    return result_json
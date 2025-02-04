import os
from openai import OpenAI
import re
import subprocess
import json
import argparse
client = OpenAI(api_key= os.environ['BAILIAN_API_KEY'] , base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")


def get_lean_import_mappings(folder_path):
    import_mappings = {}

    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith(".lean"):

                abs_path = os.path.join(root, file)

                rel_path = os.path.relpath(abs_path, folder_path)

                import_str = rel_path.replace(os.sep, ".").replace(".lean", "")

                import_str = f"import mathlib.{import_str}"

                import_mappings[import_str] = abs_path

    return import_mappings

def run_lake_build():
    try:
        result = subprocess.run(['lake', 'build'], 
                                capture_output=True, 
                                text=True, 
                                check=True,
                                cwd='.')
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running 'lake build':\n{e}")
        print(f"Error output: {e.stderr}")
        return e.stdout
    
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
            temperature=0.0
        )
    
    result = completion.choices[0].message.content
    new_result = extract_from_code_block(result)[0].strip("json").strip("<").strip(">")
    return json.loads(new_result)

def reformat_json_multi_round(text, num_round=3):
    current_round = 0
    while current_round < num_round:
        try:
            result = reformat_json(text)
            return result
        except Exception as e:
            text
            print(f"{current_round} failed")
        current_round += 1

def extract_json_from_str(str):
    result_str = str.strip("json").strip("<").strip(">")
    try:
        result_json = json.loads(result_str)
    except Exception as e:
        print(f"Exception: {e}")
        result_json = reformat_json_multi_round(result_str)
    return result_json


instruction_scheduler_prompt = '''Please read hypothesis, lean code, execution result and next-step instruction set, and choose next instruction. Requirements are as follows:
1.  Hypothesis, lean code, execution result and next-step instruction set are each located in different code blocks.
2.  Output should be in the format of "```json\n<output>```", where "<output>" is the placeholder.An example is as follows:
```json
{
    "instruction": "<instruction name>",
    "parameters": [],
}
```
3.  Next-step instrcutions (Initialize, Repair Import, Read Lean, Rewrite) are as follows:
3.1 Initialize: Generate lean code based on the original problem.
3.2 Repair Import: Fix the error in the import section of the code according to the paths of mathlib packages.
3.3 Read Lean:You should list in parameters the relevant package names of import section according to the execution result. This helps user provide specific library to guide you to fix the error. You can provide more than one relevant package names of import section in parameters of output. If you want to know more about "import mathlib.Algebra.AddConstMap.Basic", an example is as follows:
```json
{
    "instruction": "Read Lean",
    "parameters": [
        "mathlib.Algebra.AddConstMap.Basic"
    ],
}
```
3.4 Rewrite: Rewrite the lean code.
'''


def instruction_schedule(hypothesis, lean_code, execution_result):
    global client, instruction_scheduler_prompt
    content = [
        f"```hypothesis\n{hypothesis}```",
        f"```lean code\n{lean_code}```",
        f"```execution result\n{execution_result}```",
    ]
    content = "\n\n".join(content)
    completion = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {'role': 'system', 'content': instruction_scheduler_prompt},
            {'role': 'user', 'content': content}
        ],
        stream=False,
        temperature=0.0
    )
    result = completion.choices[0].message.content
    result_str_list = extract_from_code_block(result)
    result_str = result_str_list[0]
    result_json = extract_json_from_str(result_str)
    return result_json


initialize_lean_prompt = '''Please read hypothesis and follow these instructions:
1. Generate complete lean code to verify the hypothesis.
2. Ouput should be in the format of "```lean\n<output>", where "<output>" is the placeholder. An examples is as follows:
```lean
impor mathlib.Data.Real.Basic
```
3. If you want to use mathlib, please import relevant package in the format of "impot mathlib.<sub package name chain>".
4. Please import package with PascalCase name instead of underscore name.
5. The first word in import section is "mathlib" instead of "Mathlib".
6. Do not generate "import mathlib.Data.Real.Basic".
7. Use 'variable' instead of 'variables'.
8. Define 'n' as '{n : ℕ}' before use it.
9. Use the camel case naming convention for variable names.
10. Do not use placeholders such as 'sorry' or 'admit' to replace the proof process. Please complete the proof.
'''

def initialize_lean(hypothesis):
    global client, initialize_lean_prompt
    content = [
        f"```hypothesis\n{hypothesis}```"
    ]
    content = "\n\n".join(content)
    completion = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {'role': 'system', 'content': initialize_lean_prompt},
            {'role': 'user', 'content': content}
        ],
        stream=False,
        temperature=0.0
    )
    result = completion.choices[0].message.content
    result_str_list = extract_from_code_block(result)
    result_str = result_str_list[0].strip("lean").strip("<").strip(">")
    return result_str

repair_import_prompt = '''Please read hypothesis, lean code, error and valid mathlib package paths, and follow these instructions:
1. To fix the error in import section, find similar paths in valid mathlib package paths to replace the error path.
2. Generate new lean code to verify the hypothesis.
3. Ouput should be in the format of "```lean\n<output>", where "<output>" is the placeholder.
4. If there are underscores in the names within the import section, change them to PascalCase.
5. The first word in import section is "mathlib" instead of "Mathlib".
6. If an package has been contained in environment, please remove it.
'''
def repair_import(hypothesis, lean_code, error, valid_lib_paths):
    global client, repair_import_prompt
    content = [
        f"```hypothesis\n{hypothesis}```",
        f"```lean code\n{lean_code}",
        f"```error\n{error}```",
        f"```valid mathlib package paths\n{json.dumps(valid_lib_paths)}```",
    ]
    content = "\n\n".join(content)
    completion = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {'role': 'system', 'content': repair_import_prompt},
            {'role': 'user', 'content': content}
        ],
        stream=False,
        temperature=0.0
    )
    result = completion.choices[0].message.content
    result_str_list = extract_from_code_block(result)
    result_str = result_str_list[0].strip("lean").strip("<").strip(">")
    return result_str

read_lean_prompt = '''Please read hypothesis, lean code, error and relevant mathlib package, and follow these instructions:
1. To fix the error, generate new lean code to verify the hypothesis according to the relevant mathlib package.
2. Ouput should be in the format of "```lean\n<output>", where "<output>" is the placeholder.
'''

def read_lean(hypothesis, lean_code, error, mathlib_package_json):
    global client, read_lean_prompt
    content = [
        f"```hypothesis\n{hypothesis}```",
        f"```lean code\n{lean_code}",
        f"```error\n{error}```",
        f"```{mathlib_package_json['name']}\n{mathlib_package_json['text']}```",
    ]
    content = "\n\n".join(content)
    completion = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {'role': 'system', 'content': read_lean_prompt},
            {'role': 'user', 'content': content}
        ],
        stream=False,
        temperature=0.0
    )
    result = completion.choices[0].message.content
    result_str_list = extract_from_code_block(result)
    result_str = result_str_list[0].strip("lean").strip("<").strip(">")
    return result_str

rewrite_prompt = '''Please read hypothesis, lean code, error, and follow these instructions:
1. To fix the error, generate new lean code to verify the hypothesis.
2. Ouput should be in the format of "```lean\n<output>", where "<output>" is the placeholder.
3. Use 'variable' instead of 'variables'.
4. Define 'n' as '{n : ℕ}' before use it.
5. Use the camel case naming convention for variable names.
6. Do not use placeholders such as 'sorry' or 'admit' to replace the proof process. Please complete the proof.
'''

def rewrite(hypothesis, lean_code, error):
    global client, rewrite_prompt
    content = [
        f"```hypothesis\n{hypothesis}```",
        f"```lean code\n{lean_code}",
        f"```error\n{error}```",
    ]
    content = "\n\n".join(content)
    completion = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {'role': 'system', 'content': rewrite_prompt},
            {'role': 'user', 'content': content}
        ],
        stream=False,
        temperature=0.0
    )
    result = completion.choices[0].message.content
    result_str_list = extract_from_code_block(result)
    result_str = result_str_list[0].strip("lean").strip("<").strip(">")
    return result_str

def instruction_execute(hypothesis, lean_path, num_round=30):
    mathlib_dir = "./.lake/packages/mathlib/Mathlib"
    mappings = get_lean_import_mappings(mathlib_dir)
    valid_lib_paths = list(mappings.keys())
    lean_code = ""
    execution_result = ""
    current_round = 0
    while "Build completed successfully" not in execution_result and current_round < num_round:
        current_round += 1
        instruction_json = instruction_schedule(hypothesis, lean_code, execution_result)
        instruction_str = instruction_json['instruction']
        print(f"{current_round} instruction: {instruction_str}")
        if instruction_str == "Initialize":
            lean_code = initialize_lean(hypothesis)
        elif instruction_str == "Repair Import":
            lean_code = repair_import(hypothesis, lean_code, execution_result, valid_lib_paths)
        elif instruction_str == "Read Lean":
            package_name = instruction_json["parameters"][0]
            if package_name not in valid_lib_paths:
                raise Exception(f"invalid package name: {package_name}")
            package_path = mappings[package_name]
            with open(package_path, encoding='utf-8') as f:
                package_text = f.read()
            mathlib_package_json = {
                "name": package_name,
                "text": package_text
            }
            lean_code = read_lean(hypothesis, lean_code, execution_result, mathlib_package_json)

        elif instruction_str == "Rewrite":
            lean_code = rewrite(hypothesis, lean_code, execution_result, valid_lib_paths)
        else:
            raise Exception(f"invalid instruction: {instruction_str}")
        with open(lean_path, 'w', encoding='utf-8') as f:
            f.write(lean_code)
        execution_result = run_lake_build()

        print(f"{current_round} execute result: {execution_result}")

def main():
    parser = argparse.ArgumentParser(description="auto verify math")
    parser.add_argument("hypothesis", type=str, required=True, help="hypothesis path")
    parser.add_argument("lean_code", type=str, required=True, help="lean code path")

    args = parser.parse_args()

    if not os.path.exists(args.hyothesis):
        print(f"{args.hypothesis} doesn't exist")
        return
    if not os.path.exists(args.lean_code):
        print(f"{args.lean_code} doesn't exist")
        return
    
    with open(args.hypothesis, encoding='utf-8') as f:
        hypothesis = f.read()

    instruction_execute(hypothesis, args.lean_code)

if __name__ == "__main__":
    main()
from openai import OpenAI
import os

client = OpenAI(
    api_key="",
    base_url='http://localhost:11434',
)

def get_qwen_response(prompt):
    response = client.chat.completions.create(
        model='qwen2.5:0.5b',
        messages=[
            {"role":"system", "content": "你是一个开发查询问题的智能体，你的名字叫Bond"},
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content

response = get_qwen_response("你是谁")
print(response)

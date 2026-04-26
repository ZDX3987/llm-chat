from openai import OpenAI

client = OpenAI(
    api_key="",
    base_url='http://localhost:11434/v1',
)

def invoke(user_message, model_name="qwen3:1.7b"):
    response = create_response(user_message, model_name)
    return response.choices[0].message.content

def invoke_with_stream(user_message, model_name="qwen3:1.7b"):
    response = create_response(user_message, model_name, True)
    message = ""
    for result in response:
        delta = result.choices[0].delta
        if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
            print(delta.reasoning_content, end="")
        else:
            message+=delta.content
            print(delta.content, end="")
    return message

def create_response(user_message, model_name="qwen3:1.7b", stream=False):
    return client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "user", "content": user_message}
        ],
        stream=stream
    )
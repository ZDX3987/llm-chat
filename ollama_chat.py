from ollama import Client

client = Client(
    host="localhost:11434",
)


def get_ollama_response_with_prompt(message, prompt):
    stream = client.chat(model="qwen2.5:0.5b", messages=[
        {"role": "system", "content": prompt},
        {"role": "user", "content": message}
    ], stream=True,
                         # options={"temperature": 1.9, "top_p": 0.8}
                         )

    for chunk in stream:
        yield chunk.message.content

def get_ollama_response(message):
    return get_ollama_response_with_prompt(message, "你是一个辅助Java开发程序员学习LLM的一个助手，使用者名字是ZHANGDX，你需要尽可能准确的回答问题，并提供一些建议")


response = get_ollama_response("帮我生成一个技术博客的名字")
for e in response:
    print(e, end="")

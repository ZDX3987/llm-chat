from llama_index.core import PromptTemplate

import llama_chat_rag as rag

index = rag.load_index()
engine = rag.create_query_engine(index)
prompt_str = (
    "【任务目标】\n"
    "根据用户的问题，回答问题\n"
    "【角色】"
    "---------------------"
)
engine.update_prompts({"response_synthesizer:summary_template": PromptTemplate(prompt_str)})

question = '请用中文回答，这篇文章主要讲了什么'

rag.ask_llm(engine, question)
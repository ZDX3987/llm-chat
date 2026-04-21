from os.path import exists

from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama

storage_location = "knowledge_base/test"

# 指定内嵌模型，这里使用Ollama提供的内嵌模型
embed_model = OllamaEmbedding(
    model_name="nomic-embed-text",
)
# 加载存储的索引
existsStorage = exists("knowledge_base/test")
if existsStorage:
    print("正在加载索引。。。")
    storage_context = StorageContext.from_defaults(persist_dir="knowledge_base/test")
    index = load_index_from_storage(storage_context, embed_model=embed_model)
else:
    print("正在解析文件。。。")
    # LlamaIndex提供了SimpleDirectoryReader方法，可以直接将指定文件夹中的文件加载为document对象，对应着解析过程
    documents = SimpleDirectoryReader("./docs").load_data()
    # from_documents方法包含切片与建立索引步骤
    print("正在创建索引。。。")
    index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)
    # 存储索引
    index.storage_context.persist(storage_location)
print("正在创建提问引擎。。。")
query_engine = index.as_query_engine(
    # 设置为流式输出
    streaming=True,
    # 模型为本地的千问模型
    llm=Ollama(
        model="qwen3:1.7b",
    )
)

print("正在生成回复。。。")
streaming_response = query_engine.query('请用中文回答，这篇文章主要讲了什么')

print("回答是：")
# 采用流式输出
streaming_response.print_response_stream()
import llm_chat as llm

blog_read_me = """
这是一个Java后端开发者的个人技术博客，名字叫做ZHANGDX。博客由作者自己开发，后端由Java框架Spring Boot开发，前端包括两个Vue框架项目（客户端和管理端）。
整个博客系统包括：文章模块、用户模块、文章标签模块、专栏模块等和一些相关操作，系统支持动态菜单管理和权限管理。系统用户支持注册和第三方账户授权登录。
"""

initial_message = f"""
根据以下信息，回答这个博客系统相关的介绍文案。

【参考信息】
{blog_read_me}
"""

response = llm.invoke_with_stream("你是谁")
print("----初始回答---")
print(response)

meta_prompt = f"""
我在
"""
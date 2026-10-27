# 四种 API 的实测样例

姓名：郭庆　学号：f25015325

[返回实验报告](../README.md)

以下请求体、回答与耗时均来自 `20261027-140617-715154` 的实际运行记录。
服务器为 `http://127.0.0.1:11434`，模型统一使用 `qwen3:8b`。
请求的 Content-Type 为 `application/json; charset=utf-8`，并统一使用 `stream: false`。
单次耗时未控制所有推理条件，不构成性能比较。

## Ollama 原生 API

路径：`POST /api/chat`

实际请求体：

```json
{
  "model": "qwen3:8b",
  "messages": [
    {
      "role": "user",
      "content": "请用中文简要说明什么是开源大语言模型，限三句话。/no_think"
    }
  ],
  "stream": false,
  "think": false,
  "options": {
    "temperature": 0,
    "num_predict": 512
  }
}
```

文本提取：data["message"]["content"]

本次回答：

> 开源大语言模型是指基于大量文本数据训练而成的人工智能模型，其源代码和部分训练数据对公众开放。这类模型能够理解和生成自然语言，广泛应用于聊天、翻译、写作等场景。用户可以自由使用、修改和分发这些模型，促进了人工智能技术的共享与创新。

结果：**PASS**；本次耗时：**2.661 秒**。

[完整原始请求与响应](../results/20261027-140617-715154/ollama.json)

## OpenAI Chat Completions 兼容接口

路径：`POST /v1/chat/completions`

实际请求体：

```json
{
  "model": "qwen3:8b",
  "messages": [
    {
      "role": "user",
      "content": "请用中文简要说明什么是开源大语言模型，限三句话。/no_think"
    }
  ],
  "stream": false,
  "temperature": 0,
  "max_tokens": 512
}
```

文本提取：data["choices"][0]["message"]["content"]

本次回答：

> 开源大语言模型是指基于开源协议发布的大型语言模型，其代码和参数均向公众开放。这类模型通过海量文本数据训练，具备自然语言理解、生成和多任务处理能力。开发者可自由使用、修改和分发模型，推动技术透明化与社区协作创新。

结果：**PASS**；本次耗时：**6.774 秒**。

[完整原始请求与响应](../results/20261027-140617-715154/chat_completions.json)

## OpenAI Responses 兼容接口

路径：`POST /v1/responses`

实际请求体：

```json
{
  "model": "qwen3:8b",
  "input": "请用中文简要说明什么是开源大语言模型，限三句话。/no_think",
  "stream": false,
  "temperature": 0,
  "max_output_tokens": 512
}
```

文本提取：遍历 output 中 type=message 的项，再提取 content 中 type=output_text 的 text

本次回答：

> 开源大语言模型是指基于开源协议发布的大型语言模型，其代码和参数均向公众开放。这类模型通过海量文本数据训练，具备理解与生成自然语言的能力。开发者可自由使用、修改和分发模型，推动技术透明化与社区协作创新。

结果：**PASS**；本次耗时：**2.000 秒**。

[完整原始请求与响应](../results/20261027-140617-715154/responses.json)

## Anthropic Messages 兼容接口

路径：`POST /v1/messages`

本地兼容调用的附加请求头（来自实验脚本）：

```text
x-api-key: ollama
anthropic-version: 2023-06-01
```

实际请求体：

```json
{
  "model": "qwen3:8b",
  "messages": [
    {
      "role": "user",
      "content": "请用中文简要说明什么是开源大语言模型，限三句话。/no_think"
    }
  ],
  "stream": false,
  "temperature": 0,
  "max_tokens": 512
}
```

文本提取：遍历 content 中 type=text 的块，提取 text

本次回答：

> 开源大语言模型是指基于开源协议发布的大型语言模型，其代码和参数均向公众开放。这类模型通过海量文本数据训练，具备理解与生成自然语言的能力。开发者可自由使用、修改和分发模型，推动技术透明化与社区协作创新。

结果：**PASS**；本次耗时：**1.990 秒**。

[完整原始请求与响应](../results/20261027-140617-715154/anthropic_messages.json)

## 对结果的说明

这些回答是模型生成内容，按原文保留以支持实验核查；关于开源范围的概括不应当作权威定义，见主报告中的内容核查。
Responses 记录中还存在 reasoning 类型输出项，程序按类型提取最终文本。
本实验使用的 `ollama` 请求头值是本地占位符，不是云服务密钥。

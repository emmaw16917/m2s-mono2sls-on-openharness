import requests
import json

print("✅ 脚本已启动")

api_key = "sk-ws-H.IMDYRMM.N71J.MEQCIGc_wkD8cp4cK8MAqUHD3_8uAlTVHMU5mcYtqn4ot9qQAiBDUhpRHZmZtjdE8n4fdylEf9zb2F3-WV7tXoZMuigbZA"
print("✅ API 密钥已加载")

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

data = {
    "model": "qwen-plus",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
}

print("⏳ 正在发送请求到 Qwen API...")

try:
    response = requests.post(
        "https://ws-zpqs2kv79br02kev.us-east-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        headers=headers,
        json=data,
        timeout=90
    )
    print(f"✅ 状态码: {response.status_code}")
    print(f"📄 响应内容:\n{response.text}")
except requests.exceptions.Timeout:
    print("❌ 错误: 请求超时（90秒内未收到响应）")
except requests.exceptions.ConnectionError:
    print("❌ 错误: 无法连接到服务器（网络问题）")
except Exception as e:
    print(f"❌ 错误: {type(e).__name__}: {e}")
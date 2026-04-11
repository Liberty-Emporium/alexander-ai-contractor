"""
AI CEO for Contractor Pro
Uses user-provided API keys from database - tries ALL available keys
"""

import os
import requests

class AICEO:
    def __init__(self, api_keys=None, active_provider='qwen'):
        self.name = "Contractor AI"
        self.api_keys = api_keys or {}
        self.active_provider = active_provider
    
    def think(self, prompt):
        system_prompt = """You are the CEO of Contractor Pro - an app for contractors.
Your job is to help with:
- Writing bids
- Finding best prices
- Answering contractor questions
- Business advice

Be specific, helpful, and professional."""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # Try ALL providers - use whichever has a valid key
        providers = ['anthropic', 'qwen', 'groq', 'openai', 'xai', 'mistral']
        
        for prov in providers:
            key = self.api_keys.get(f'{prov}_key', '')
            if not key or len(key) < 10:  # Skip empty or too-short keys
                continue
            
            try:
                if prov == 'groq':
                    response = requests.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": "llama-3.3-70b-versatile", "messages": messages, "max_tokens": 500},
                        timeout=30
                    )
                elif prov == 'qwen':
                    response = requests.post(
                        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": "qwen-plus", "messages": messages, "max_tokens": 500},
                        timeout=30
                    )
                elif prov == 'anthropic':
                    response = requests.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                        json={"model": "claude-3-haiku-20240307", "max_tokens": 500, "messages": messages},
                        timeout=30
                    )
                elif prov == 'openai':
                    response = requests.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": "gpt-3.5-turbo", "messages": messages, "max_tokens": 500},
                        timeout=30
                    )
                elif prov == 'xai':
                    response = requests.post(
                        "https://api.x.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": "grok-beta", "messages": messages, "max_tokens": 500},
                        timeout=30
                    )
                elif prov == 'mistral':
                    response = requests.post(
                        "https://api.mistral.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": "mistral-small-latest", "messages": messages, "max_tokens": 500},
                        timeout=30
                    )
                else:
                    continue
                
                if response.status_code == 200:
                    if prov == 'anthropic':
                        return response.json()['content'][0]['text']
                    return response.json()['choices'][0]['message']['content']
                else:
                    # Key might be invalid
                    print(f"API error for {prov}: {response.status_code} - {response.text}")
            except Exception as e:
                print(f"Exception for {prov}: {e}")
                continue
        
        return "AI unavailable - no valid API keys found"

# Default instance
ceo = AICEO()

"""
AI CEO for Contractor Pro
Uses user-provided API keys from database
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
        
        # Try the active provider first
        provider = self.active_provider
        
        # Try each provider in order of preference
        providers = [provider] + [p for p in ['qwen', 'groq', 'anthropic', 'openai', 'xai', 'mistral'] if p != provider]
        
        for prov in providers:
            key = self.api_keys.get(f'{prov}_key', '')
            if not key:
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
            except Exception as e:
                continue
        
        return "AI unavailable - configure API keys in Settings"

# Default instance for backward compatibility
ceo = AICEO()

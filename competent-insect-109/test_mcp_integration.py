import asyncio
import os
from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from agent.mcp_registry import get_all_toolsets

load_dotenv()

# 1. Initialize OpenRouter Provider with custom base_url
openrouter_provider = OpenAIProvider(
    base_url='https://openrouter.ai/api/v1',
    api_key=os.getenv('OPENROUTER_API_KEY')
)

# 2. Initialize Pydantic AI Model using the provider
openrouter_model = OpenAIModel(
    'openai/gpt-oss-120b:free',
    provider=openrouter_provider
)

# 3. Initialize Agent with the System Prompt and MCP Toolsets
test_agent = Agent(
    model=openrouter_model,
    system_prompt="""You are a test dispatch agent. 
    You must use the search_shelters and evaluate_physical_compatibility tools.
    If a user mentions a wheelchair, you MUST call evaluate_physical_compatibility.""",
    toolsets=get_all_toolsets()
)

async def run_integration_test():
    print("🚀 Starting MCP Integration Test over ngrok...")
    print("🔗 Connecting to OpenRouter and MCP Server...")
    
    test_prompt = "I need a shelter in Zone A for 1 person. They have a motorized wheelchair."
    
    try:
        result = await test_agent.run(test_prompt)
        
        print("\n✅ SUCCESS: Agent completed execution!")
        print(f"🤖 Final LLM Output: {result.data}\n")
        
        print("--- RAW TOOL CALL HISTORY ---")
        for msg in result.all_messages():
            if msg.role == 'tool':
                for part in msg.parts:
                    if hasattr(part, 'tool_name'):
                        print(f"🛠️ Tool '{part.tool_name}' returned: {part.content}")
                
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        print("Check if ngrok is running and the LIFELINE_MCP_URL in .env is correct.")

if __name__ == "__main__":
    asyncio.run(run_integration_test())
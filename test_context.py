import os
import sys
import types

# Mock psycopg2 since it might not be installed in the test env
psycopg2 = types.ModuleType('psycopg2')
psycopg2.extras = types.ModuleType('psycopg2.extras')
psycopg2.pool = types.ModuleType('psycopg2.pool')
psycopg2.pool.ThreadedConnectionPool = object
sys.modules['psycopg2'] = psycopg2
sys.modules['psycopg2.extras'] = psycopg2.extras
sys.modules['psycopg2.pool'] = psycopg2.pool

flask = types.ModuleType('flask')
flask.Flask = lambda *a, **k: type('MockFlask', (), {'secret_key': None, 'config': {}, 'get': lambda *a, **k: lambda f: f, 'post': lambda *a, **k: lambda f: f, 'after_request': lambda f: f})()
flask.request = type('MockRequest', (), {'args': {}, 'form': {}, 'headers': {}, 'get_data': lambda *a, **k: "", 'get_json': lambda *a, **k: {}})
flask.jsonify = lambda *a, **k: "{}"
flask.Response = lambda *a, **k: None
flask.redirect = lambda *a, **k: None
flask.url_for = lambda *a, **k: None
flask.render_template = lambda *a, **k: None
flask.session = {}
sys.modules['flask'] = flask

flask_limiter = types.ModuleType('flask_limiter')
flask_limiter.Limiter = lambda *a, **k: type('MockLimiter', (), {'limit': lambda *a, **k: lambda f: f})()
sys.modules['flask_limiter'] = flask_limiter
sys.modules['flask_limiter.util'] = types.ModuleType('flask_limiter.util')
sys.modules['flask_limiter.util'].get_remote_address = lambda: "127.0.0.1"

# Fake envs for safe testing
os.environ["OPENAI_API_KEY"] = "fake"
os.environ["DATAFY_WEBHOOK_SECRET"] = "fake"
os.environ["DB_DRIVER"] = "postgresql"

import memory
import app

def test_scenario(name, last_assistant, user_input, expected_confirmation):
    print(f"\n--- {name} ---")
    user = f"test_{name}"
    
    # Mock history
    memory.reset(user)
    if last_assistant:
        memory.append(user, "assistant", last_assistant)
        
    items = [{"text": user_input}]
    
    # We will hook into app.llm.generate_reply to inspect what gets sent
    original_generate = app.llm.generate_reply
    
    captured_prompt = ""
    def mock_generate_reply(history, system, tools=None, tool_handlers=None):
        nonlocal captured_prompt
        captured_prompt = system
        
        # Also test if RAG query gets expanded
        if tool_handlers and "search_knowledge" in tool_handlers:
            tool_handlers["search_knowledge"]("coxinha")
            
        return "Mocked reply"
        
    app.llm.generate_reply = mock_generate_reply
    
    # Mock RAG handler to see if it gets expanded
    original_rag = app._knowledge_tool_handler
    captured_query = ""
    def mock_rag(query="", filename=""):
        nonlocal captured_query
        captured_query = query
        return "Mocked RAG result"
    app._knowledge_tool_handler = mock_rag
    
    # Mock transfer handler
    app.perform_transfer = lambda *args, **kwargs: True
    
    # Mock database settings
    original_get_setting = app.database.get_setting
    app.database.get_setting = lambda k, d="": "1" if k == "agent_mode" else d
    
    original_get_contact = app.database.get_contact
    app.database.get_contact = lambda u: {"auto_mode": 1, "name": "Test User"}
    
    try:
        app.handle_flush(user, items)
    except Exception as e:
        print(f"Error: {e}")
        
    is_detected = "CONTEXTO DE CONFIRMAÇÃO DETECTADO" in captured_prompt
    
    print(f"Assistant: {last_assistant}")
    print(f"User: {user_input}")
    print(f"Confirmation Detected: {is_detected}")
    print(f"RAG Query: {captured_query}")
    
    if is_detected == expected_confirmation:
        print("✅ PASS")
    else:
        print(f"❌ FAIL (Expected {expected_confirmation}, got {is_detected})")

if __name__ == "__main__":
    # Test 1: Confirming 1 portion of 25
    test_scenario(
        "TEST 1", 
        "Você quer 1 porção de 25 unidades?", 
        "1 de 25 mesmo", 
        True
    )
    
    # Test 2: Simple sim
    test_scenario(
        "TEST 2", 
        "Você quer 1 porção de 25 unidades?", 
        "sim", 
        True
    )
    
    # Test 3: Confirming individual
    test_scenario(
        "TEST 3", 
        "Você quer 3 coxinhas individuais?", 
        "sim", 
        True
    )
    
    # Test 4: Explicit change, not a confirmation of the same thing (though it might still trigger the context block, the LLM will read the text "não, quero 10 individuais")
    test_scenario(
        "TEST 4", 
        "Você quer 1 porção de 25?", 
        "não, quero 10 individuais", 
        False # 'não' is not in keywords, so shouldn't be detected as pure confirmation
    )
    
    # Test 5: Fresh start
    test_scenario(
        "TEST 5", 
        "", 
        "Quero 25 coxinhas", 
        False
    )
    
    # Test 6: Fresh start with portion word
    test_scenario(
        "TEST 6", 
        "", 
        "Quero 1 porção de 25 coxinhas.", 
        False
    )
    
    # Test 7: Fresh start individual
    test_scenario(
        "TEST 7", 
        "", 
        "Quero 2 coxinhas.", 
        False
    )
    
    # Test 8: Fresh start portion word
    test_scenario(
        "TEST 8", 
        "", 
        "Quero uma porção.", 
        False
    )

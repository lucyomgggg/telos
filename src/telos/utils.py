import json
from .logger import get_logger

log = get_logger("utils")

def repair_json(json_str: str) -> str:
    """Attempt to repair common LLM malformations in JSON."""
    if not json_str:
        return "{}"
        
    json_str = json_str.strip()
    
    # 1. Remove markdown code blocks if present
    if "```" in json_str:
        import re
        # Find content between ```json and ``` or just ``` and ```
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', json_str, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
    
    # 2. Extract the first { and last } if there is surrounding text
    try:
        start = json_str.find('{')
        end = json_str.rfind('}')
        if start != -1 and end != -1:
            json_str = json_str[start:end+1]
    except Exception:
        pass
    
    # 3. Handle common minor issues
    # Replace unescaped newlines in strings (very common failure)
    # This is a bit risky but often helpful. Use a simple regex for content between quotes.
    import re
    def fix_newlines(match):
        return match.group(0).replace('\n', '\\n').replace('\r', '\\r')
    
    json_str = re.sub(r'"[^"]*"', fix_newlines, json_str)
    
    # 4. Remove trailing commas before closing brackets/braces
    json_str = re.sub(r',\s*([\]}])', r'\1', json_str)
        
    return json_str

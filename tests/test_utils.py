import pytest
from src.telos.utils import repair_json

def test_repair_json_clean():
    clean = '{"key": "value"}'
    assert repair_json(clean) == clean

def test_repair_json_markdown():
    md = '```json\n{"key": "value"}\n```'
    assert repair_json(md) == '{"key": "value"}'
    
    md_no_lang = '```\n{"key": "value"}\n```'
    assert repair_json(md_no_lang) == '{"key": "value"}'

def test_repair_json_surrounding_text():
    text = 'Here is the result: {"key": "value"} Hope it helps!'
    assert repair_json(text) == '{"key": "value"}'

def test_repair_json_complex_markdown():
    text = 'Thinking... ```json {"a": 1} ``` Done.'
    assert repair_json(text) == '{"a": 1}'

def test_repair_json_no_json():
    text = 'No json here'
    assert repair_json(text) == text

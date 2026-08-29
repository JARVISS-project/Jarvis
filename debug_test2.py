import asyncio
from tests.tools.test_browser import FakeBrowserBackend, build_service, browser_tools
from jarvis.tools.executor import ToolExecutor
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from pathlib import Path

service = build_service(enabled=True)
print('Service created')

# Launch browser and create tab synchronously
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
try:
    loop.run_until_complete(service.launch())
    print('Launched')
    tab = loop.run_until_complete(service.new_tab('https://example.com'))
    print(f'Tab created: {tab.id}')
    tab_id = tab.id
finally:
    loop.close()

# Check tabs
loop2 = asyncio.new_event_loop()
asyncio.set_event_loop(loop2)
try:
    tabs = loop2.run_until_complete(service.list_tabs())
    print(f'Tabs: {tabs}')
finally:
    loop2.close()

# Now test the tool directly
from tests.tools.test_browser import browser_tools

tool = next(t for t in browser_tools(service) if t.name == 'browser_navigate')
print(f'Tool: {tool.name}')

# Test tool execute directly
loop3 = asyncio.new_event_loop()
asyncio.set_event_loop(loop3)
try:
    result = loop3.run_until_complete(service.navigate(tab_id, 'https://example.com'))
    print(f'Navigate result: {result}')
finally:
    loop3.close()
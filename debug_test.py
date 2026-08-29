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

# Now test executor
from tests.tools.test_browser import browser_tools
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from pathlib import Path

tool = next(t for t in browser_tools(service) if t.name == 'browser_navigate')
audit_path = Path('test_audit.log')
executor = ToolExecutor(
    SafetyManager(),
    AuditLogger(audit_path),
    capability_policy=CapabilityPolicy({Capability.BROWSER: True}),
)

result = executor.execute_with_result(tool, url='https://example.com', tab_id=tab_id)
print(f'Result status: {result.status}')
print(f'Result content: {result.content}')
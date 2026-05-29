import re
with open('web_ui/templates/index.html') as f:
    html = f.read()
scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
print(f'Found {len(scripts)} script blocks')
for i, s in enumerate(scripts):
    lines = s.split('\n')
    print(f'  Block {i}: {len(lines)} lines')
    print(f'    let _lastStatus count: {lines.count("let _lastStatus = {};")}')
    print(f'    _lastStatus = s count: {lines.count("_lastStatus = s;")}')
    print(f'    updateUI count: {len(re.findall(r"function updateUI", s))}')
    print(f'    level listener: {len(re.findall(r"socket.on.*level", s))}')
print('Done')

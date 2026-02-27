"""Patch v3: use native data-id from Zhihu DOM instead of hash"""
with open('browser_crawler.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the FNV-1a hash block and replace with data-id extraction
old_marker = 'FNV-1a'
found = False
for i, line in enumerate(lines):
    if old_marker in line:
        print(f"Found FNV block at line {i+1}")
        # Find start of the block (the comment line with "生成稳定")
        start = i - 1  # line before FNV-1a comment
        # Find end (the commentId line)
        end = i
        while end < len(lines):
            if 'commentId' in lines[end] and '=' in lines[end]:
                break
            end += 1
        print(f"Replacing lines {start+1} to {end+1}")
        
        new_lines = [
            "                // \u4ece DOM \u7684 data-id \u5c5e\u6027\u83b7\u53d6\u77e5\u4e4e\u539f\u751f\u8bc4\u8bba ID\n",
            "                let commentId = '';\n",
            "                let parentId = null;\n",
            "                let el = contentEl.parentElement;\n",
            "                let foundSelf = false;\n",
            "                while (el) {\n",
            "                    const did = el.getAttribute('data-id');\n",
            "                    if (did) {\n",
            "                        if (!foundSelf) {\n",
            "                            commentId = did;\n",
            "                            foundSelf = true;\n",
            "                        } else {\n",
            "                            parentId = did;\n",
            "                            break;\n",
            "                        }\n",
            "                    }\n",
            "                    el = el.parentElement;\n",
            "                }\n",
            "                if (!commentId) commentId = 'b_' + index;\n",
        ]
        lines[start:end+1] = new_lines
        found = True
        break

if not found:
    print("ERROR: FNV block not found")
else:
    # Also fix the results.push to use parentId from DOM
    content = ''.join(lines)
    # Replace parent_id: null with parent_id: parentId (from data-id)
    old_push = "parent_id: null,"
    new_push = "parent_id: parentId,"
    if old_push in content:
        content = content.replace(old_push, new_push, 1)
        print("Also updated parent_id to use DOM parentId")
    
    with open('browser_crawler.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("OK: patched successfully")
    
    # Verify
    with open('browser_crawler.py', 'r', encoding='utf-8') as f:
        text = f.read()
    idx = text.find('data-id')
    print(f"Verification: 'data-id' found at position {idx}")

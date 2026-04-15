# Email Formatting Issue - Complete Debug Log

## Problem Description
Emails sent via Gmail API have unwanted hard line breaks in the middle of paragraphs at approximately 70-80 characters, making them look broken on all email clients (desktop, mobile, web).

Example of the issue:
```
Oh, I love talking about movies! I have such a wide range of favorites
depending on my mood. I'm really drawn to films that have beautiful
cinematography and thoughtful storytelling.
```

**Expected:** Continuous paragraphs that wrap naturally based on recipient's email client width
**Actual:** Hard line breaks mid-sentence that appear on all devices

---

## Attempt #1: AI Prompt Instructions
**Date:** First attempt in this session
**Location:** `promaia/mail/response_generator.py`

### What I Did
Added instruction to AI prompt:
```python
IMPORTANT: Write in natural flowing paragraphs. Do NOT add hard line breaks within paragraphs.
```

### Why It Failed
The AI was already generating continuous text. The problem wasn't the AI's output - it was something happening during the email sending process.

---

## Attempt #2: Text Formatting Function
**Date:** Second attempt
**Location:** `promaia/mail/response_generator.py`

### What I Did
1. Created `_format_email_body()` method to remove hard line breaks
2. Applied formatting after AI generation in `generate_response()` 
3. Applied formatting after refinement in `refine_response()`

```python
def _format_email_body(self, text: str) -> str:
    """Remove unnecessary hard line breaks from email body"""
    # Logic to join lines within paragraphs
    # Preserve blank lines between paragraphs
    # Preserve list item formatting
```

### Why It Failed
The formatting function worked perfectly in isolation (tested and verified), but the hard line breaks were STILL appearing in sent emails. This meant the problem was happening AFTER our formatting, during the actual email sending.

---

## Attempt #3: Format Before Sending (Draft Chat)
**Date:** Third attempt
**Location:** `promaia/mail/draft_chat.py` line 502

### What I Did
Added formatting right before sending in `_handle_send_command()`:
```python
draft_to_send = self.artifacts[draft_num]
draft_to_send = self.response_generator._format_email_body(draft_to_send)
```

### Why It Failed
Still didn't work. The formatting was being applied, but something in the Gmail API layer was re-wrapping the text.

---

## Attempt #4: Format in Review UI
**Date:** Fourth attempt  
**Location:** `promaia/mail/review_ui.py` line 451

### What I Did
Added same formatting to the backup send method in review UI (though this path wasn't being used).

### Why It Failed
Not the active code path, so no effect.

---

## Attempt #5: Python Cache Clearing
**Date:** Multiple attempts throughout session

### What I Did
```bash
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -name "*.pyc" -delete
```

### Why It Failed
While necessary, cache clearing alone didn't fix the root cause.

---

## Attempt #6: Process Restart
**Date:** Multiple attempts

### What I Did
- Killed all Python processes
- Restarted `maia mail`
- Verified old processes were gone

### Why It Failed
User kept seeing the same issue even with fresh processes.

---

## Attempt #7: THE ROOT CAUSE DISCOVERED
**Date:** Major breakthrough
**Location:** `promaia/connectors/gmail_connector.py` line 1462

### What I Found
```python
message = MIMEText(body_text)
raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
```

**The Problem:** Python's `MIMEText` class automatically wraps text at 78 characters for RFC 5322 compliance! The `.as_bytes()` method uses a default generator that adds hard line breaks.

### The Fix I Implemented
```python
# Create message
message = MIMEText(body_text, _charset='utf-8')
message.set_param('format', 'flowed')
message['Content-Type'] = 'text/plain; charset=utf-8; format=flowed'

# Use custom policy to prevent line wrapping
from email import generator
from io import BytesIO

policy_no_wrap = policy.EmailPolicy(max_line_length=None)
fp = BytesIO()
g = generator.BytesGenerator(fp, policy=policy_no_wrap)
g.flatten(message)
raw_message = base64.urlsafe_b64encode(fp.getvalue()).decode('utf-8')
```

### Test Results
Ran isolated tests that showed the fix working perfectly:
- Input text with long paragraphs
- Output had continuous lines (182 chars, 214 chars, etc.)
- No unwanted breaks
- Text decoded correctly from base64

---

## Attempt #8: Package Reinstall
**Date:** Final attempt
**Location:** Virtual environment

### What I Did
```bash
source venv/bin/activate
pip uninstall -y promaia
pip install -e .
```

### Result
Package reinstalled successfully with new code confirmed in place.

---

## Current Status

### Verified Working
- ✅ `_format_email_body()` function works in isolation
- ✅ `EmailPolicy(max_line_length=None)` is in the code (line 1489)
- ✅ Text formatting removes hard breaks correctly
- ✅ All Python cache cleared
- ✅ Package reinstalled in venv
- ✅ All old processes killed

### Still Failing
- ❌ Emails sent via `maia mail` still show hard line breaks
- ❌ Issue appears on all devices (mobile, desktop, web)
- ❌ Issue persists even after all fixes and restarts

---

## Hypothesis: Why It's Still Not Working

### Possibility 1: Code Not Being Used
The installed package might not be using the updated source code. The `-e` flag should make it editable, but something might be cached.

### Possibility 2: Multiple Installation Locations
There might be multiple installations of promaia:
- System-wide installation
- Venv installation  
- Development installation

The `maia` command might be running the wrong one.

### Possibility 3: Gmail API Ignoring Our Policy
Gmail's API or the MIME parser might be re-processing the email and adding line breaks regardless of our `max_line_length=None` setting.

### Possibility 4: Base64 Encoding Issue
The base64 encoding itself might be wrapping, though this is unlikely since base64 encoding happens after the policy is applied.

### Possibility 5: Format=flowed Not Respected
Modern email clients might not properly respect the `format=flowed` parameter, or Gmail might be stripping it.

---

## Files Modified

1. `promaia/mail/response_generator.py`
   - Added `_format_email_body()` method
   - Added formatting calls after generation and refinement
   - Updated AI prompts

2. `promaia/mail/draft_chat.py`
   - Added formatting before send (line 502)
   - Added carriage return removal (line 304)

3. `promaia/mail/review_ui.py`
   - Added formatting before send (line 451)

4. `promaia/connectors/gmail_connector.py`
   - **CRITICAL:** Changed MIME encoding to use `EmailPolicy(max_line_length=None)`
   - Added imports: `BytesIO`, `generator`, `policy`
   - Modified `send_email()` method (lines 1462-1493)

---

## Next Steps to Debug

### 1. Verify Which Code Is Actually Running
```bash
python3 -c "import promaia.connectors.gmail_connector; import inspect; print(inspect.getfile(promaia.connectors.gmail_connector))"
```

### 2. Add Debug Logging
Add print statements in `gmail_connector.py` before sending to see the actual text being sent:
```python
print(f"DEBUG: Body text before MIME: {body_text[:200]}")
print(f"DEBUG: Encoded message length: {len(raw_message)}")
```

### 3. Inspect Actual Sent Email Headers
Download a sent email's raw source from Gmail to see:
- What Content-Type was actually sent
- Whether format=flowed is present
- What the actual line breaks look like in the raw MIME

### 4. Test with Simple Python Script
Create standalone script that uses the exact same Gmail API code to send a test email, verify it works outside of the maia application.

### 5. Check Gmail API Documentation
Research if there are specific Gmail API requirements or limitations that override MIME policies.

---

## Test Commands Used

```bash
# Clear cache
find . -type d -name "__pycache__" -exec rm -rf {} +

# Kill processes
pkill -9 -f "python.*promaia"

# Reinstall package
source venv/bin/activate
pip uninstall -y promaia
pip install -e .

# Verify code
grep -n "EmailPolicy(max_line_length=None)" promaia/connectors/gmail_connector.py
```

---

## Conclusion

Despite implementing what should be the correct fix (using `EmailPolicy(max_line_length=None)` to prevent MIME text wrapping), the issue persists. The most likely explanation is that either:

1. The updated code is not being executed (wrong installation being used)
2. Gmail's API or infrastructure is re-processing the email 
3. There's an additional layer of text processing we haven't identified

Further debugging requires:
- Confirming which code path is actually executing
- Adding debug logging to trace the exact text being sent
- Examining raw email source from Gmail to see what was actually transmitted


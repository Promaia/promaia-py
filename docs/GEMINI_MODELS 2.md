# Gemini Model Configuration

This document explains how to use different Gemini models (including Gemini 2.0) with MAIA.

## Available Gemini Models

MAIA now supports multiple Gemini model variants:

### Gemini 2.5 (Current Generation)
- **pro** → `gemini-2.5-pro-preview-05-06` - Gemini 2.5 Pro (default)
- **flash** → `gemini-2.5-flash-preview-05-20` - Gemini 2.5 Flash (faster, cheaper)

### Gemini 2.0 (Newer Generation)
- **2.0-flash** → `gemini-2.0-flash-exp` - Gemini 2.0 Flash (experimental)
- **2.0-flash-thinking** → `gemini-2.0-flash-thinking-exp-1219` - Gemini 2.0 with extended reasoning

### Experimental Models
- **exp-1206** → `gemini-exp-1206` - December 2024 experimental model
- **exp-1121** → `gemini-exp-1121` - November 2024 experimental model

## Configuration Methods

### Method 1: Environment Variable (Recommended)

Set the `GOOGLE_DEFAULT_MODEL` environment variable in your `.env` file or shell:

```bash
# Use a key name (easier)
export GOOGLE_DEFAULT_MODEL='2.0-flash'

# Or use the full model ID
export GOOGLE_DEFAULT_MODEL='gemini-2.0-flash-exp'
```

**Key Names:**
```bash
GOOGLE_DEFAULT_MODEL='pro'                    # Gemini 2.5 Pro (default)
GOOGLE_DEFAULT_MODEL='flash'                  # Gemini 2.5 Flash
GOOGLE_DEFAULT_MODEL='2.0-flash'              # Gemini 2.0 Flash
GOOGLE_DEFAULT_MODEL='2.0-flash-thinking'     # Gemini 2.0 Flash Thinking
GOOGLE_DEFAULT_MODEL='exp-1206'               # Experimental (Dec 2024)
GOOGLE_DEFAULT_MODEL='exp-1121'               # Experimental (Nov 2024)
```

**Full Model IDs:**
```bash
GOOGLE_DEFAULT_MODEL='gemini-2.5-pro-preview-05-06'
GOOGLE_DEFAULT_MODEL='gemini-2.5-flash-preview-05-20'
GOOGLE_DEFAULT_MODEL='gemini-2.0-flash-exp'
GOOGLE_DEFAULT_MODEL='gemini-2.0-flash-thinking-exp-1219'
GOOGLE_DEFAULT_MODEL='gemini-exp-1206'
GOOGLE_DEFAULT_MODEL='gemini-exp-1121'
```

### Method 2: Update .env File

Add or update the line in your `.env` file:

```bash
GOOGLE_DEFAULT_MODEL='2.0-flash'
```

Then restart your MAIA session.

## Usage Examples

### Using Gemini 2.0 Flash

```bash
# Set the environment variable
export GOOGLE_DEFAULT_MODEL='2.0-flash'

# Start MAIA chat
maia chat
```

### Using Gemini 2.0 Flash Thinking (Extended Reasoning)

```bash
# Set the environment variable
export GOOGLE_DEFAULT_MODEL='2.0-flash-thinking'

# Start MAIA chat
maia chat
```

### Using Experimental Models

```bash
# Try the latest experimental model
export GOOGLE_DEFAULT_MODEL='exp-1206'

# Start MAIA chat
maia chat
```

## Model Selection in Chat

When you start a chat session, you can still switch models using the `/model` command:

```
You: /model

Available models:
  1. Claude Sonnet 4.5
  2. GPT-4o
  3. Gemini 2.0 Flash (current)
  4. Llama 3

Select model (1-4): 3
```

The model shown for Gemini will reflect your `GOOGLE_DEFAULT_MODEL` setting.

## Quick Switching Models

You can also set the environment variable for a single command:

```bash
# Use Gemini 2.0 for one chat session
GOOGLE_DEFAULT_MODEL='2.0-flash' maia chat

# Use Gemini 2.5 Pro for another
GOOGLE_DEFAULT_MODEL='pro' maia chat -w koii
```

## Model Comparison

| Model | Speed | Cost | Best For |
|-------|-------|------|----------|
| Gemini 2.5 Pro | Medium | Medium | General purpose, complex tasks |
| Gemini 2.5 Flash | Fast | Low | Quick responses, simple tasks |
| Gemini 2.0 Flash | Very Fast | Very Low | Speed-optimized tasks |
| Gemini 2.0 Flash Thinking | Slow | Medium | Complex reasoning, problem-solving |
| Experimental | Varies | Varies | Testing new features |

## Checking Current Model

To see which model is currently active:

```bash
# In chat, the model is displayed in the header
🐙 maia chat
Model: Gemini 2.0 Flash

# Or check with /model command
You: /model
```

## Troubleshooting

### Model Not Found Error

If you get an error like "Model not found", the model ID might be incorrect or not yet available in your region.

**Solution:** Use one of the verified model IDs listed above.

### API Key Issues

Make sure your `GOOGLE_API_KEY` is set correctly:

```bash
echo $GOOGLE_API_KEY
```

If empty, set it in your `.env` file:

```bash
GOOGLE_API_KEY='your_google_api_key_here'
```

### Model Access Restrictions

Some experimental models may require waitlist access or have regional restrictions.

**Solution:** Check Google AI Studio for model availability in your account.

## Additional Resources

- [Google AI Studio](https://ai.google.dev/)
- [Gemini API Documentation](https://ai.google.dev/docs)
- [MAIA Environment Variables](./env.template)

## Notes

- Gemini 2.0 models are experimental and may change
- Model availability varies by region
- Pricing and rate limits differ by model
- Default model (if not specified) is Gemini 2.5 Pro






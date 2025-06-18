# Contributing to LLM Telephone

Thank you for your interest in contributing to LLM Telephone! This guide will help you get started.

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- OpenRouter API key

### Setup Development Environment

1. **Clone the repository**
   ```bash
   git clone https://github.com/smusali/llm-telephone.git
   cd llm-telephone
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure API key**
   ```bash
   export OPENROUTER_API_KEY="your-api-key-here"
   ```

5. **Test the setup**
   ```bash
   python telephone.py --start "Test message"
   ```

## 📋 Project Structure

```
llm-telephone/
├── telephone.py          # Main CLI entry point
├── llm_tel/             # Core package
│   ├── engine.py        # Telephone game orchestration
│   ├── client.py        # API communication
│   ├── judge.py         # LLM-as-a-Judge scoring
│   ├── prompts.py       # Template management
│   ├── config.py        # System configuration
│   └── persistence.py   # Data storage
├── experiments/         # Generated experiment data
└── requirements.txt     # Dependencies
```

## 🧪 Running Tests

Currently, the project uses manual testing. To test:

```bash
# Test single experiment
python telephone.py --start "Redis uses single-threaded event loops"

# Check experiments directory for output
ls experiments/
```

## 🎯 How to Contribute

### Reporting Issues
- Use clear, descriptive titles
- Include Python version and OS
- Provide steps to reproduce
- Share error messages and logs

### Submitting Changes

1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Make your changes**
   - Follow existing code style
   - Add docstrings for new functions
   - Update relevant documentation

4. **Test your changes**
   ```bash
   python telephone.py --start "Test input"
   ```

5. **Commit and push**
   ```bash
   git commit -m "Add: brief description of changes"
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request**
   - Describe what your changes do
   - Reference any related issues
   - Include testing steps

## 📝 Code Style Guidelines

- **Python**: Follow PEP 8
- **Imports**: Group standard library, third-party, and local imports
- **Functions**: Use type hints and docstrings
- **Comments**: Explain *why*, not *what*
- **Line length**: 88 characters maximum

### Example Function
```python
def process_response(text: str, model: str) -> dict[str, str]:
    """Process LLM response and extract relevant information.
    
    Args:
        text: Raw response text from LLM
        model: Model identifier used for the response
        
    Returns:
        Processed response data with metadata
    """
    # Implementation here
    return {"text": text.strip(), "model": model}
```

## 🐛 Common Issues

**API Hanging**: Some OpenRouter models may hang on certain inputs. This is a known limitation.

**Missing Dependencies**: Run `pip install -r requirements.txt` to ensure all dependencies are installed.

**Permission Errors**: Ensure write permissions for the `experiments/` directory.

## 💡 Contribution Ideas

- Improve timeout handling for API calls
- Add support for additional LLM providers
- Create experiment analysis tools
- Implement better error recovery
- Add visualization capabilities
- Improve documentation

## 📞 Getting Help

- Open an issue for bugs or questions
- Check existing issues before creating new ones
- Be patient and respectful in communications

## 📄 License

By contributing, you agree that your contributions will be licensed under the same license as the project.

---

**Happy Contributing!** 🎉 

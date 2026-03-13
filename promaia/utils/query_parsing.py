"""
Utility functions for parsing command-line query arguments.
"""
import json

from promaia.utils.env_writer import get_config_path


def parse_vs_queries_with_params(argv):
    """
    Parse -vs queries with their per-query -tk/-th parameters from an argv list.

    Args:
        argv: List of command-line arguments (e.g., sys.argv or shlex.split result)

    Returns:
        List of dicts with structure: {'query': str, 'top_k': int, 'threshold': float}

    Raises:
        ValueError: If query appears to be mangled by shell parsing
    """
    vs_queries_structured = []

    # Find all -vs positions and pair them with following -tk/-th
    vs_indices = [i for i, arg in enumerate(argv) if arg in ['-vs', '--vector-search']]

    for idx_pos, vs_idx in enumerate(vs_indices):
        # Get the query text (everything after -vs until next flag)
        query_parts = []
        i = vs_idx + 1
        while i < len(argv) and not argv[i].startswith('-'):
            query_parts.append(argv[i])
            i += 1
        query_text = ' '.join(query_parts)

        # Detect potential shell parsing issues
        if not query_text.strip():
            raise ValueError(
                "Empty vector search query detected. This often happens when special shell "
                "characters (like parentheses, quotes, or brackets) aren't properly quoted.\n\n"
                "Please quote your query:\n"
                "  maia chat -vs \"your query with (special) characters\" -tk 1\n\n"
                "Or escape special characters:\n"
                "  maia chat -vs your query with \\(special\\) characters -tk 1"
            )

        # Check for common shell parsing artifacts
        suspicious_patterns = [
            ('with', 'h'),  # "with (text)" becomes "wit" + "h" in zsh glob parsing
            ('Shared', 'h'),  # Similar pattern
        ]

        # Check if query looks suspiciously short given the context
        if len(query_parts) < 3 and any(part in ['with', 'name', 'containing'] for part in query_parts):
            print(f"\n⚠️  WARNING: Your query '{query_text}' looks incomplete.")
            print("This often happens when parentheses or other special characters aren't quoted.")
            print("\nIf your query should contain parentheses like '(text)', please:")
            print("  1. Quote the entire query: -vs \"query with (text)\"")
            print("  2. Or escape them: -vs query with \\(text\\)")
            print("\nPress Enter to continue with this query, or Ctrl+C to cancel...\n")
            try:
                input()
            except KeyboardInterrupt:
                print("\n\nCancelled.")
                raise SystemExit(0)

        # Find -tk and -th for this query (between this -vs and next -vs)
        next_vs_idx = vs_indices[idx_pos + 1] if idx_pos + 1 < len(vs_indices) else len(argv)

        # Load defaults from config
        try:
            with open(str(get_config_path()), 'r') as f:
                config = json.load(f)
                top_k = config.get('vector_search', {}).get('default_n_results', 20)
                threshold = config.get('vector_search', {}).get('default_similarity_threshold', 0.2)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            # Fallback to reasonable defaults if config unavailable
            top_k = 50
            threshold = 0.2

        # Look for -tk/-th in the range after this query's text
        for j in range(i, next_vs_idx):
            if argv[j] in ['-tk', '--top-k'] and j + 1 < len(argv):
                try:
                    top_k = int(argv[j + 1])
                except ValueError:
                    pass
            elif argv[j] in ['-th', '--threshold'] and j + 1 < len(argv):
                try:
                    threshold = float(argv[j + 1])
                except ValueError:
                    pass

        vs_queries_structured.append({
            'query': query_text,
            'top_k': top_k,
            'threshold': threshold
        })

    return vs_queries_structured

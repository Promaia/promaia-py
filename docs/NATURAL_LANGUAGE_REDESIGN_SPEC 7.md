# Promaia Natural Language Query System - Complete Redesign Specification

## Executive Summary

This document outlines the complete redesign of Promaia's natural language query system, replacing the current Vanna.ai-based implementation with a robust, schema-aware, performance-optimized LangGraph architecture.

## Current Problems Analysis

### 1. Architectural Issues
- **State pollution** between browse (-b) and natural language (-nl) modes
- **Browser incorrectly triggered** when it shouldn't be
- **Persistent page loading** after removing natural language queries
- **Complex mixed command logic** creating unpredictable behavior

### 2. Vanna.ai Limitations (2024 Reviews)
- **Syntax errors** in complex queries
- **Table identification struggles** with complex schemas
- **175+ hard-coded examples** approach doesn't generalize
- **Limited enterprise viability** for multi-database scenarios
- **Poor performance** on complex schemas like Promaia's multi-workspace setup

### 3. Hard-coded "Vibe Coding" Problems
- 175+ training examples in `promaia/ai/natural_query.py` lines 105-176
- Overly verbose schema instructions trying to override AI behavior
- Complex mixed command detection logic across multiple files
- Byzantine state management preserving too much context

## User Intent & Use Cases

### Primary Use Case
**Complex time-based queries that are painful to type:**
```bash
"grab me a month of journal entries from each month since December 2024"
```

**Current SQL equivalent (horrible to type):**
```sql
SELECT * FROM unified_content 
WHERE database_name = 'journal' 
AND created_time >= '2024-12-01' 
AND ((SUBSTR(created_time, 1, 7) = '2024-12' AND CAST(SUBSTR(created_time, 9, 2) AS INTEGER) <= 31) 
  OR (SUBSTR(created_time, 1, 7) = '2025-01' AND CAST(SUBSTR(created_time, 9, 2) AS INTEGER) <= 31)
  OR (SUBSTR(created_time, 1, 7) = '2025-02' AND CAST(SUBSTR(created_time, 9, 2) AS INTEGER) <= 28)
  -- ... repeat for every month
```

### Core Value Proposition
Natural language serves as **syntax simplification**, not browser replacement:
- **High-Value**: Complex time ranges, cross-database queries, quantity-based sampling
- **Browser handles**: Simple selections, exploration, one-off queries  
- **Combined usage**: `-b trass -nl "monthly samples since December 2024"`

### Query Examples

#### Complex Time Patterns
```bash
maia chat -nl "monthly journal entries since December 2024"
maia chat -nl "a few entries from each week" 
maia chat -nl "weekly samples since March"
```

#### Cross-Database Queries
```bash  
maia chat -b trass -nl "emails and journal entries about promaia"
maia chat -nl "discord messages and notion stories about project X"
```

#### Content vs Metadata Intelligence
```bash
# Content search (includes body/content fields)
maia chat -nl 'emails that have the sentence "the quick brown fox jumped"'
maia chat -nl "discord messages containing word puppies"
maia chat -nl "notion pages that mention promaia development"

# Metadata search (excludes heavy content fields for performance)  
maia chat -nl "find emails from Fionn"
maia chat -nl "journal entries from last week"
maia chat -nl "notion stories with status archived"
```

#### Rich Property Queries
```bash
maia chat -nl "notion stories with status of archived"
maia chat -nl "emails in koii workspace that contain word puppies" 
maia chat -nl "discord messages with high reactions from last week"
```

## Proposed Architecture

### 1. Core Design Principles

#### Scope + Intent Separation
```python
# Clean separation of concerns
class PromaiaQueryPipeline:
    def parse_command(self, args) -> QuerySpec:
        """
        -b trass -nl "last couple weeks of koii journal"
        ↓
        scope: ["trass workspace"] 
        intent: "journal entries, recent, ~2 weeks"
        """
    
    def resolve_scope(self, scope_spec) -> DatabaseList:
        """Browse component: which databases to search"""
        
    def resolve_intent(self, intent, databases) -> ExecutionPlan:
        """NL component: what to find within scope"""
        
    def execute(self, plan) -> Results:
        """Get the actual data"""
```

#### Pattern-Based Processing
```python
class PatternBasedNLProcessor:
    """Focus on painful-to-type patterns, not general NL"""
    
    COMMON_PATTERNS = {
        # Primary use case
        "monthly_samples": r"(month|monthly).*(since|from)\s+(\w+\s+\d{4})",
        "weekly_samples": r"(week|weekly).*(since|from)",
        "date_ranges": r"(last|past)\s+(\d+)\s+(days?|weeks?|months?)",
        "cross_source": r"(\w+)\s+and\s+(\w+).*(about|containing)",
        "quantity_based": r"(few|several|some|smattering|handful)",
        "content_search": r"(contains?|containing|has|have)\s+(sentence|word|phrase)",
        "metadata_search": r"(from|by|sent by|created by)\s+(\w+)",
    }
```

### 2. LangGraph Implementation

#### Core Workflow
```python
class SchemaAwareNLGraph:
    """LangGraph implementation with goal-oriented retry"""
    
    def create_workflow(self) -> StateGraph:
        workflow = StateGraph(QueryState)
        
        # Main processing pipeline
        workflow.add_node("parse_intent", self.parse_natural_language)
        workflow.add_node("classify_query_type", self.classify_query_intent)
        workflow.add_node("resolve_schema", self.get_relevant_schemas)
        workflow.add_node("generate_optimized_query", self.generate_optimized_query)
        workflow.add_node("validate_results", self.check_goal_accomplished) 
        workflow.add_node("refine_query", self.fix_and_retry)
        
        # Retry logic - keeps trying until goal accomplished
        workflow.add_conditional_edges(
            "validate_results",
            self.should_retry,
            {"retry": "refine_query", "done": END}
        )
        
        return workflow
```

#### State Management
```python
class QueryState(TypedDict):
    user_query: str
    intent: QueryIntent
    query_type: QueryType  # CONTENT_REQUIRED, METADATA_ONLY
    available_schemas: Dict[str, Schema]
    generated_sql: str
    execution_results: List[Dict]
    error_history: List[str]
    retry_count: int
    status: str  # "processing", "retry_needed", "success"
```

### 3. Schema Intelligence

#### Multi-DataType Schema Provider
```python
class MultiDataTypeSchemaProvider:
    """Context from ALL relational sub-tables"""
    
    def get_full_schema_context(self, intent: QueryIntent) -> SchemaContext:
        schemas = {}
        
        # Universal table (unified_content) 
        schemas["universal"] = self.get_universal_schema()
        
        # Data-type specific relational tables
        if "notion" in intent.sources:
            schemas["notion"] = {
                "notion_properties": "status, priority, custom select fields",
                "notion_select_values": "actual values for select properties",
                "notion_page_content": "full page content for text search"
            }
            
        if "gmail" in intent.sources:  
            schemas["gmail"] = {
                "gmail_messages": "sender_email, subject, body_text, thread_id",
                "gmail_threads": "thread relationships",
                "gmail_attachments": "file attachments"
            }
            
        if "discord" in intent.sources:
            schemas["discord"] = {
                "discord_messages": "channel, author, message_content",
                "discord_reactions": "reaction counts and types", 
                "discord_threads": "thread relationships"
            }
            
        return SchemaContext(schemas)
```

#### Schema-Aware SQL Generation
```python
def generate_notion_property_query(self, property_name: str, property_value: str) -> str:
    """
    'notion stories with status of archived' 
    → Query unified_content + notion_properties + notion_select_values
    """
    return f"""
    SELECT u.page_id, u.title, u.created_time, u.workspace,
           np.property_name, nsv.select_value
    FROM unified_content u
    JOIN notion_properties np ON u.page_id = np.page_id
    JOIN notion_select_values nsv ON np.property_id = nsv.property_id
    WHERE u.content_type = 'notion'
    AND np.property_name = '{property_name}'
    AND nsv.select_value = '{property_value}'
    """
```

### 4. Content vs Metadata Intelligence

#### Query Classification
```python
class QueryClassifier:
    """Detect whether to query content, metadata, or both"""
    
    CONTENT_INDICATORS = [
        r"(contains?|containing|includes?|including|has|have)\s+(the\s+)?(word|phrase|sentence|text)",
        r"(says?|mentions?|discusses?|talks about)",
        r"(body|content|message)\s+(contains?|has|includes?)",
        r'"[^"]{3,}"',  # Quoted strings - explicit text search
        r"(full text|body text|message content)",
    ]
    
    METADATA_ONLY_INDICATORS = [
        r"(from|to|sent by|received from|by)\s+(\w+)",
        r"(sender|recipient|author|creator)(\s+is|\s+equals?)?",
        r"(sent|created|received|posted)\s+(on|at|in|during)",
        r"(status|priority|category|tag|label)(\s+is|\s+equals?)?",
        r"(in|from)\s+(channel|folder|inbox|sent)",
    ]

    def classify_query_type(self, query: str) -> QueryType:
        """Determine if we need content, metadata, or both"""
        
        for pattern in self.CONTENT_INDICATORS:
            if re.search(pattern, query, re.IGNORECASE):
                return QueryType.CONTENT_REQUIRED
                
        for pattern in self.METADATA_ONLY_INDICATORS:
            if re.search(pattern, query, re.IGNORECASE):
                return QueryType.METADATA_ONLY
                
        return QueryType.METADATA_ONLY  # Default to performance-optimized
```

#### Performance-Optimized SQL Generation
```python
def generate_metadata_query(self, intent: QueryIntent) -> str:
    """Metadata-only - excludes heavy content fields"""
    return f"""
    SELECT u.page_id, u.title, u.created_time, u.workspace,
           g.sender_email, g.sender_name, g.subject, 
           g.thread_id, g.message_id
           -- DELIBERATELY EXCLUDE g.body_text, g.body_html
    FROM unified_content u
    JOIN gmail_messages g ON u.page_id = g.page_id  
    WHERE u.content_type = 'gmail'
    AND g.sender_name LIKE '%{intent.person_filter}%'
    """

def generate_content_query(self, intent: QueryIntent) -> str:
    """Full content search - includes body/content fields"""  
    return f"""
    SELECT u.page_id, u.title, u.created_time, u.workspace,
           g.sender_email, g.subject, g.body_text, g.body_html
    FROM unified_content u  
    JOIN gmail_messages g ON u.page_id = g.page_id
    WHERE u.content_type = 'gmail'
    AND g.body_text LIKE '%{intent.search_terms[0]}%'
    """
```

### 5. Goal-Oriented Retry Logic

#### Retry Mechanisms
```python
async def validate_results(self, state: QueryState) -> QueryState:
    """Check if query accomplished the goal"""
    try:
        results = self.execute_sql(state["generated_sql"])
        
        if self.goal_accomplished(results, state["intent"]):
            state["final_results"] = results
            state["status"] = "success"
        else:
            state["status"] = "retry_needed"
            state["error_history"].append("Results don't match intent")
            
    except Exception as e:
        state["status"] = "retry_needed"  
        state["error_history"].append(str(e))
        state["retry_count"] += 1
        
    return state

def should_retry(self, state: QueryState) -> str:
    """LangGraph conditional - retry or done"""
    if state["retry_count"] >= 3:
        return "done"  # Max retries reached
    return "retry" if state["status"] == "retry_needed" else "done"

async def refine_query(self, state: QueryState) -> QueryState:
    """Fix query based on previous errors"""
    error_context = "\n".join(state["error_history"])
    
    refined_prompt = f"""
    Previous attempt failed with errors: {error_context}
    Original intent: {state['intent']}
    
    Generate a corrected SQL query that addresses these issues.
    """
    
    state["generated_sql"] = await self.llm.ainvoke(refined_prompt)
    return state
```

#### Error Recovery Strategies
- **Parse Error** → Retry with better structured prompt
- **SQL Syntax Error** → Fix syntax and re-execute  
- **Wrong Results** → Refine query understanding
- **No Results** → Broaden search or suggest alternatives
- **Schema Error** → Use correct table/column names from schema

## Implementation Plan

### Phase 1: Foundation (Week 1)
**Goal: Stable base architecture**

1. **Remove Vanna.ai completely**
   - Delete 175+ training examples from `promaia/ai/natural_query.py`
   - Remove Vanna client initialization
   - Clean up hard-coded SQL generation logic

2. **Implement Pattern-Based Core**
   - Create `PatternBasedNLProcessor` class
   - Add regex patterns for top 5 use cases
   - Implement monthly sampling logic (primary use case)

3. **Fix State Management**
   - Separate scope resolution from intent processing
   - Clean up browse mode state pollution 
   - Remove persistent context bugs

### Phase 2: LangGraph Integration (Week 2)
**Goal: Robust retry logic and schema awareness**

1. **Build LangGraph Workflow**
   - Create `SchemaAwareNLGraph` class
   - Implement core nodes: parse, classify, generate, validate, retry
   - Add conditional edges for retry logic

2. **Add Schema Intelligence**  
   - Create `MultiDataTypeSchemaProvider`
   - Map universal + data-type specific schemas
   - Implement schema-aware SQL generation

3. **Content vs Metadata Classification**
   - Create `QueryClassifier` with regex patterns
   - Add performance-optimized query generation
   - Test metadata-only vs content-required queries

### Phase 3: Enhanced Features (Week 3)
**Goal: Production-ready with advanced capabilities**

1. **Cross-Database Queries**
   - Handle multiple source types in single query
   - Implement proper JOINs across data types
   - Add workspace filtering logic

2. **Smart Quantity Handling**
   - Map quantity words ("smattering", "few") to numbers
   - Implement smart sampling for time ranges
   - Add result validation for quantity expectations

3. **Advanced Error Recovery**
   - Add schema validation before SQL execution
   - Implement progressive refinement strategies
   - Add user clarification prompts for ambiguous queries

### Phase 4: Integration & Testing (Week 4)
**Goal: Seamless integration with existing system**

1. **CLI Integration**
   - Update command parsing in `promaia/cli.py`
   - Ensure `-b` + `-nl` combinations work correctly
   - Test all interaction modes

2. **Chat Interface Integration**  
   - Update chat interface in `promaia/chat/interface.py`
   - Remove old mixed command logic
   - Add new NL query handling

3. **Performance Optimization**
   - Add query caching for repeated patterns
   - Optimize schema loading
   - Add query execution timeouts

## Technical Dependencies

### Required Libraries
```python
# Core dependencies
langgraph>=0.0.40
langchain>=0.1.0
pydantic>=2.0.0

# Enhanced SQL parsing
sqlparse>=0.4.0

# Performance monitoring
asyncio
typing_extensions
```

### Database Schema Requirements
- **Universal Table**: `unified_content` (already exists)
- **Data-Type Tables**: 
  - `notion_properties`, `notion_select_values`, `notion_page_content`
  - `gmail_messages`, `gmail_threads`, `gmail_attachments`  
  - `discord_messages`, `discord_reactions`, `discord_threads`

## Success Metrics

### Performance Goals
- **Query Response Time**: < 2 seconds for metadata queries
- **Content Search Time**: < 10 seconds for full-text searches
- **Retry Success Rate**: > 90% of queries succeed within 3 attempts
- **Pattern Recognition**: > 95% accuracy for common query patterns

### User Experience Goals  
- **Syntax Simplification**: Complex queries 10x easier to type
- **Reliability**: No more browser triggering bugs
- **Predictability**: Consistent behavior across query types
- **Performance**: Fast metadata queries, targeted content searches

### Architecture Goals
- **Maintainability**: No hard-coded examples to maintain
- **Extensibility**: Easy to add new query patterns
- **Observability**: Clear debugging and error tracing
- **Scalability**: Handles increasing data volume efficiently

## Risk Mitigation

### Technical Risks
1. **LangGraph Learning Curve**: Start with simple workflow, add complexity gradually
2. **Schema Complexity**: Build comprehensive test suite for schema changes
3. **Performance Regression**: Benchmark against current system continuously
4. **LLM Reliability**: Add fallback to simple pattern matching

### Business Risks  
1. **User Adoption**: Maintain backward compatibility during transition
2. **Feature Parity**: Ensure new system matches all current capabilities
3. **Data Integrity**: Extensive testing to prevent data corruption
4. **Deployment Risk**: Gradual rollout with feature flags

## Future Enhancements

### Advanced Natural Language
- **Multi-step Queries**: "Find emails from Fionn about project X, then show related journal entries"
- **Temporal Reasoning**: "Show me the conversation thread that started last Tuesday"
- **Contextual Search**: "Find similar documents to this one"

### Integration Enhancements
- **Voice Input**: Natural language queries via speech recognition
- **Query Suggestions**: Auto-complete based on query patterns
- **Result Refinement**: Interactive query refinement based on results

### Performance Optimizations
- **Query Caching**: Cache frequent query patterns
- **Index Optimization**: Add database indices for common query patterns  
- **Parallel Execution**: Run multiple queries concurrently when possible

---

## Conclusion

This redesign replaces a brittle, hard-coded system with a robust, schema-aware, performance-optimized architecture that focuses on the real user need: **making complex queries simple to express**. The LangGraph-based approach provides reliability through retry logic while maintaining the flexibility to handle diverse query patterns across Promaia's multi-database environment.

The key insight is treating natural language as **syntax simplification** rather than general AI, focusing on the patterns that are genuinely painful to type while maintaining high performance for common operations.
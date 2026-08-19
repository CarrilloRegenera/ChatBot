# CLAUDE.md

Directrices de comportamiento para reducir errores comunes de los LLM al programar. Combínalas con instrucciones específicas del proyecto según sea necesario.

**Compromiso:** Estas directrices priorizan la cautela sobre la velocidad. Para tareas triviales, usa tu criterio.

## 1. Piensa antes de programar

**No supongas. No ocultes la confusión. Haz visibles las compensaciones.**

Antes de implementar:
- Expón tus suposiciones de forma explícita. Si no estás seguro, pregunta.
- Si existen múltiples interpretaciones, preséntalas; no elijas una en silencio.
- Si existe un enfoque más simple, dilo. Lleva la contraria cuando esté justificado.
- Si algo no está claro, detente. Nombra qué es lo que genera confusión. Pregunta.

## 2. La simplicidad primero

**El mínimo código que resuelve el problema. Nada especulativo.**

- No añadas funcionalidades más allá de lo que se pidió.
- No crees abstracciones para código de un solo uso.
- No añadas "flexibilidad" ni "configurabilidad" que no se hayan solicitado.
- No añadas manejo de errores para escenarios imposibles.
- Si escribes 200 líneas y podría resolverse con 50, reescríbelo.

Pregúntate: "¿Un ingeniero senior diría que esto está sobrecomplicado?" Si la respuesta es sí, simplifica.

## 3. Cambios quirúrgicos

**Toca solo lo que debas. Limpia solo tu propio desorden.**

Al editar código existente:
- No "mejores" código, comentarios ni formato adyacentes.
- No refactorices cosas que no están rotas.
- Sigue el estilo existente, aunque tú lo harías de otra manera.
- Si detectas código muerto no relacionado, menciónalo; no lo elimines.

Cuando tus cambios dejen elementos huérfanos:
- Elimina imports, variables o funciones que TUS cambios hayan dejado sin uso.
- No elimines código muerto preexistente a menos que te lo pidan.

La prueba: Cada línea modificada debe poder trazarse directamente hasta la petición del usuario.

## 4. Ejecución guiada por objetivos

**Define criterios de éxito. Repite el ciclo hasta verificar.**

Transforma las tareas en objetivos verificables:
- "Añadir validación" → "Escribe pruebas para entradas no válidas y luego haz que pasen"
- "Corregir el bug" → "Escribe una prueba que lo reproduzca y luego haz que pase"
- "Refactorizar X" → "Asegúrate de que las pruebas pasan antes y después"

Para tareas de varios pasos, indica un plan breve:
```
1. [Paso] → verificar: [comprobación]
2. [Paso] → verificar: [comprobación]
3. [Paso] → verificar: [comprobación]
```

Los criterios de éxito sólidos te permiten iterar de forma independiente. Los criterios débiles ("haz que funcione") requieren aclaraciones constantes.

---

**Estas directrices están funcionando si:** hay menos cambios innecesarios en los diffs, menos reescrituras por sobrecomplicación, y las preguntas de aclaración aparecen antes de implementar en lugar de después de cometer errores.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.

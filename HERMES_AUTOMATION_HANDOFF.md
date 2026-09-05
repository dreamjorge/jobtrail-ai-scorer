# Handoff: automatización de JobTrail con Hermes

## Objetivo

Continuar desde el scorer publicado y dejar una ejecución automática y segura
que evalúe empleos de JobTrail usando Hermes.

Repositorio público: <https://github.com/dreamjorge/jobtrail-ai-scorer>

## Primera tarea: corregir configuración de Hermes

Revisar `src/jobtrail_ai_scorer/config.py` y `src/jobtrail_ai_scorer/main.py`.

`AppConfig` no declara actualmente estos campos:

- `hermes_executable`
- `hermes_profile`
- `provider_timeout_seconds`

Pydantic descarta esos valores YAML, por lo que `_make_provider()` termina
usando defaults. Implementar los campos, escribir primero tests que fallen y
después corregir la implementación.

## Después de la corrección

1. Instalar y validar el ejecutable/perfil real de Hermes.
2. Confirmar que Hermes recibe el prompt por stdin y devuelve únicamente JSON
   compatible con `ScoreResult`.
3. Configurar `jobtrail_base_url` y comprobar conectividad desde el runtime.
4. Revisar si JobTrail requiere headers o token de autenticación.
5. Elegir scheduler: cron, systemd timer u OpenClaw cron.
6. Ejecutar primero con `--dry-run`; después automatizar con `--limit` y logs.
7. Decidir dónde correr Hermes:
   - host con Hermes instalado,
   - imagen Docker que incluya Hermes/modelo,
   - servicio Hermes separado.
8. Añadir observabilidad y alertas para errores de Hermes, timeouts y API.

## Comandos de verificación

```bash
pytest -q
ruff check src tests
jobtrail-ai-scorer score --config config.yaml --dry-run --limit 1
```

## Restricciones

- Usar TDD y revisión independiente antes de cerrar.
- No incluir perfiles reales, secretos, tokens ni rutas privadas.
- No ejecutar `docker compose down -v`.
- Mantener separadas las capas de provider, API y orquestación.

## Estado conocido

- Suite existente: 68 tests pasan y `ruff` está limpio.
- El repositorio público ya está en la rama `main`.
- PRs upstream de JobTrail: [#2](https://github.com/kaylaehman/jobtrail/pull/2)
  y [#4](https://github.com/kaylaehman/jobtrail/pull/4).


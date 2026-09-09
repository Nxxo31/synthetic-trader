# Contribuyendo a este proyecto

Gracias por tu interés en contribuir! Por favor, sigue estas pautas para asegurar un proceso de colaboración fluido y efectivo.

## 📋 Cómo contribuir

1. **Fork** el repositorio
2. **Clona** tu fork: `git clone https://github.com/tu-usuario/repo.git`
3. **Crea una rama** para tu feature/bugfix: `git checkout -b feature/mi-nueva-funcionalidad` o `git checkout -b fix/bug-description`
4. **Implementa** tus cambios siguiendo las convenciones del proyecto
5. **Asegúrate de pasar los gates de verificación**:
   - **Layer 1**: `typecheck`, `lint`, `build` deben pasar sin errores
   - **Layer 2**: Tests de runtime (iniciar el sistema, hacer peticiones reales)
   - **Layer 3**: Tests adversariales (límites, concurrencia, idempotencia)
6. **Actualiza la documentación** si es necesario (PROJECT.md, README, etc.)
7. **Haz commit** con un mensaje claro y descriptivo en español
8. **Push** a tu fork: `git push origin feature/mi-nueva-funcionalidad`
9. **Abre un Pull Request** hacia la rama `main` del repositorio original

## 🔍 Revisión de Pull Requests

Todos los PRs deben pasar por revisión antes de ser mergeados. El proceso incluye:

1. **Auto-revisión**: El autor revisa su propio código contra esta guía
2. **Revisión por subagente**: Un agente de IA revisa el código buscando bugs, mejoras y cumplimiento de standards
3. **Revisión humana**: El mantenedor revisa el PR y deja feedback
4. **Checks automáticos**: GitHub Actions verifica los 3 layers de prueba

## 📝 Guía de commits

Usa el formato: `<tipo>: <descripción>`

Tipos permitidos:
- `feat`: Nueva funcionalidad
- `fix`: Corrección de bug
- `docs`: Cambios en documentación
- `style`: Formato, espacios, tabs, etc. (sin cambio de lógica)
- `refactor`: Refactorización de código
- `perf`: Mejora de rendimiento
- `test`: Añadir o modificar tests
- `chore`: Cambios en build, herramientas, dependencias, etc.

Ejemplos:
- `feat: agrega endpoint de upload de evidencia ciudadana`
- `fix: corrige error de tipo en validación de JWT`
- `docs: actualiza PROJECT.md con decisiones de arquitectura`

## 🧪 Estándares de calidad

- **Ningún comentario TODO** en código de producción
- **Ningún console.log** en código de producción (usa logger estructurado)
- **Ningún magic number** — usar constantes nombradas
- **Ningún archivo .env** en control de versiones (solo .env.example)
- **Todas las variables de entorno** deben tener valores por defecto seguros o fallar explícitamente
- **Ningún código duplicado** — extraer a funciones o módulos reutilizables

## ⚠️ Lo que NO se acepta

- Commits que rompen `typecheck`, `lint` o `build`
- PRs sin tests (cuando aplique)
- Código que no sigue el estilo del proyecto (usar Prettier/ESLint para TS/JS, gofmt para Go, etc.)
- Información sensible en el código (API keys, passwords, tokens)

## ❓ Preguntas frecuentes

**¿Necesito correr los tests localmente antes de enviar un PR?**  
Sí, siempre. Los CI fallarán si no pasan los tests, pero es mejor detectar problemas temprano.

**¿Qué pasa si mi PR tiene conflictos con main?**  
Rebasea tu rama contra main y resuelve los conflictos antes de pedir review.

**¿Puedo solicitar una feature sin implementarla?**  
Sí, abre un issue con la etiqueta `enhancement` y describe tu idea.

## 🙏 Agradecimientos

Gracias por contribuir a hacer este proyecto mejor. Tu esfuerzo ayuda a mantener el código limpio, seguro y profesional.

---
*Actualizado: 2026-07-31*
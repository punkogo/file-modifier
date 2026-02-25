{{/*
Custom helpers for app-chart.
All helpers here are prefixed with "app.custom.*"
*/}}

{{/*
Extra env items for the app container.
Returns a list of env items (- name: ...) ready for nindent.
*/}}
{{- define "app.custom.extraEnvItems.app" -}}
{{- if .Values.customizations.enabled }}
- name: CUSTOM_APP_VAR
  value: "custom-app-value"
- name: CUSTOM_APP_FEATURE
  value: "enabled"
{{- end }}
{{- end }}

{{/*
Extra env items for the worker container.
Returns a list of env items (- name: ...) ready for nindent.
*/}}
{{- define "app.custom.extraEnvItems.worker" -}}
{{- if .Values.customizations.enabled }}
- name: CUSTOM_WORKER_VAR
  value: "custom-worker-value"
- name: CUSTOM_WORKER_QUEUE
  value: "custom-queue"
{{- end }}
{{- end }}

{{/*
Sidecar container items.
Returns container items ready to append to containers list.
*/}}
{{- define "app.custom.sidecarItems" -}}
{{- if .Values.customizations.enabled }}
- name: sidecar-logger
  image: "fluent/fluent-bit:latest"
  resources: {}
{{- end }}
{{- end }}

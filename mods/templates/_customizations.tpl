{{/*
Custom helpers for upstream-chart customizations.
*/}}

{{- define "app.custom.extraEnvItems.app" -}}
{{- if .Values.customizations.enabled }}
- name: CUSTOM_APP_VAR
  value: "custom-app-value"
{{- end }}
{{- end }}

{{- define "app.custom.extraEnvItems.worker" -}}
{{- if .Values.customizations.enabled }}
- name: CUSTOM_WORKER_VAR
  value: "custom-worker-value"
{{- end }}
{{- end }}

{{- define "app.custom.sidecarItems" -}}
{{- if .Values.customizations.enabled }}
- name: sidecar
  image: busybox
  command: ["sh", "-c", "echo sidecar running"]
{{- end }}
{{- end }}

      initContainers:
        - name: init-setup
          image: busybox
          command: ['sh', '-c', 'echo init done']

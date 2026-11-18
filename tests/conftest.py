import os
import tempfile

# Os testes escrevem o log numa pasta temporária, nunca no logs/ de verdade.
# Precisa vir antes de qualquer import de monitoring.registro.
os.environ["MONITOR_LOG_DIR"] = tempfile.mkdtemp(prefix="logs_teste_")

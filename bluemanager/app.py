import subprocess
import time
import re
import threading
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# --- CONFIGURAÇÃO DE PRIORIDADE ---
PRIORITY_1_BT = "bluez_sink"
PRIORITY_2_P2 = "bcm2835"
PRIORITY_3_HAT = "googlevoicehat"


def run_command(command):
    """Roda um comando no terminal e retorna a saida"""
    try:
        result = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, timeout=30)
        return result.decode('utf-8')
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except subprocess.CalledProcessError as e:
        return e.output.decode('utf-8')


def get_sink_name(pattern, sinks_output):
    """Procura um nome completo de sink baseado num trecho"""
    match = re.search(f"({pattern}[^\\s]*)", sinks_output, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def move_all_streams_to_sink(sink_name):
    """Move todos os streams de audio ativos para o sink especificado"""
    try:
        sink_inputs = run_command("pactl list sink-inputs short")
        for line in sink_inputs.strip().split('\n'):
            if line:
                input_id = line.split()[0]
                run_command(f"pactl move-sink-input {input_id} {sink_name}")
    except Exception as e:
        print(f"Erro ao mover streams: {e}")


def audio_manager_loop():
    """Vigia constante para garantir a prioridade do audio"""
    print("--- Iniciando Gerente de Prioridade de Audio ---")
    while True:
        try:
            sinks = run_command("pactl list sinks short")
            current_default = run_command("pactl get-default-sink").strip()
            
            target_sink = None
            
            if PRIORITY_1_BT in sinks.lower():
                target_sink = get_sink_name(PRIORITY_1_BT, sinks)
            elif PRIORITY_2_P2 in sinks.lower():
                target_sink = get_sink_name(PRIORITY_2_P2, sinks)
            elif PRIORITY_3_HAT in sinks.lower():
                target_sink = get_sink_name(PRIORITY_3_HAT, sinks)
            
            if target_sink and target_sink != current_default:
                print(f"[AutoSwitch] Trocando saida para: {target_sink}")
                run_command(f"pactl set-default-sink {target_sink}")
                run_command(f"pactl set-sink-volume {target_sink} 100%")
                run_command(f"pactl set-sink-mute {target_sink} 0")
                move_all_streams_to_sink(target_sink)
                
        except Exception as e:
            print(f"Erro no audio manager: {e}")
            
        time.sleep(5)


# Inicia o gerente em segundo plano
manager_thread = threading.Thread(target=audio_manager_loop, daemon=True)
manager_thread.start()


# --- FUNÇÕES AUXILIARES ---

def parse_devices(raw_output):
    """Parse da lista de dispositivos bluetooth"""
    devices = []
    regex = r"Device ([0-9A-F:]{17}) (.*)"
    lines = raw_output.split('\n')
    for line in lines:
        match = re.search(regex, line)
        if match:
            mac = match.group(1)
            name = match.group(2).strip()
            if name and name != mac.replace(":", "-"):
                devices.append({"mac": mac, "name": name})
    return devices


def ensure_bluetooth_modules():
    """Garante que os modulos bluetooth do PulseAudio estao carregados"""
    modules = run_command("pactl list modules short")
    
    if "module-bluetooth-discover" not in modules:
        print("Carregando module-bluetooth-discover...")
        run_command("pactl load-module module-bluetooth-discover")
    
    if "module-bluetooth-policy" not in modules:
        print("Carregando module-bluetooth-policy...")
        run_command("pactl load-module module-bluetooth-policy")


def reload_bluetooth_modules():
    """Recarrega os modulos bluetooth do PulseAudio"""
    print("Recarregando modulos bluetooth do PulseAudio...")
    run_command("pactl unload-module module-bluetooth-discover")
    run_command("pactl unload-module module-bluetooth-policy")
    time.sleep(1)
    run_command("pactl load-module module-bluetooth-discover")
    run_command("pactl load-module module-bluetooth-policy")
    time.sleep(2)


def wait_for_bluetooth_audio(mac, timeout=25):
    """Aguarda o audio bluetooth ficar disponivel"""
    mac_under = mac.replace(':', '_')
    
    for attempt in range(timeout):
        # Procura o card
        cards_out = run_command("pactl list cards short")
        
        # Verifica se o card existe (case insensitive)
        card_match = re.search(rf"(bluez_card\.{mac_under}[^\s]*)", cards_out, re.IGNORECASE)
        
        if card_match:
            card_name = card_match.group(1)
            print(f"Card encontrado: {card_name}")
            
            # Tenta configurar o perfil A2DP
            for profile in ["a2dp-sink", "a2dp_sink", "a2dp"]:
                result = run_command(f"pactl set-card-profile {card_name} {profile}")
                if "Failure" not in result:
                    print(f"Perfil {profile} aplicado")
                    break
                time.sleep(0.3)
            
            time.sleep(1)
            
            # Verifica se o sink apareceu
            sinks_out = run_command("pactl list sinks short")
            sink_match = re.search(rf"(bluez_sink\.{mac_under}[^\s]*)", sinks_out, re.IGNORECASE)
            
            if sink_match:
                return sink_match.group(1)
        
        print(f"Aguardando audio bluetooth... ({attempt+1}/{timeout})")
        time.sleep(1)
    
    return None


def configure_audio_sink(sink_name):
    """Configura o sink como padrao e ajusta volume"""
    print(f"Configurando sink: {sink_name}")
    run_command(f"pactl set-default-sink {sink_name}")
    run_command(f"pactl set-sink-volume {sink_name} 80%")
    run_command(f"pactl set-sink-mute {sink_name} 0")
    move_all_streams_to_sink(sink_name)


# --- ROTAS DO FLASK ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/scan')
def scan():
    """Escaneia dispositivos bluetooth disponiveis"""
    ensure_bluetooth_modules()
    
    # Inicia scan
    subprocess.Popen(["bluetoothctl", "scan", "on"])
    time.sleep(5)
    subprocess.Popen(["bluetoothctl", "scan", "off"])
    
    # Lista dispositivos
    output = run_command("bluetoothctl devices")
    devices = parse_devices(output)
    
    return jsonify(devices)


@app.route('/paired')
def paired():
    """Lista dispositivos pareados"""
    output = run_command("bluetoothctl paired-devices")
    devices = parse_devices(output)
    return jsonify(devices)


@app.route('/connected')
def connected():
    """Verifica se ha dispositivo conectado"""
    output = run_command("bluetoothctl info")
    
    if "Missing device address" in output:
        return jsonify({"connected": False, "device": None})
    
    mac_match = re.search(r"Device ([0-9A-F:]{17})", output)
    name_match = re.search(r"Name: (.*)", output)
    connected_match = re.search(r"Connected: yes", output)
    
    if mac_match and name_match and connected_match:
        return jsonify({
            "connected": True,
            "mac": mac_match.group(1),
            "name": name_match.group(1)
        })
    
    return jsonify({"connected": False, "device": None})


@app.route('/pair/<mac>')
def pair(mac):
    """Pareia e conecta a um dispositivo bluetooth"""
    mac_under = mac.replace(':', '_')
    
    print(f"\n{'='*50}")
    print(f"Iniciando pareamento com: {mac}")
    print(f"{'='*50}\n")
    
    # 1. Garante modulos carregados
    ensure_bluetooth_modules()
    
    # 2. Remove pareamento anterior (limpa estado)
    print("Limpando pareamento anterior...")
    run_command(f"bluetoothctl remove {mac}")
    time.sleep(1)
    
    # 3. Trust primeiro (permite reconexao automatica)
    print("Configurando trust...")
    run_command(f"bluetoothctl trust {mac}")
    time.sleep(0.5)
    
    # 4. Pair (para dispositivos como Bose, geralmente nao precisa de PIN)
    print("Pareando...")
    run_command(f"bluetoothctl pair {mac}")
    time.sleep(3)
    
    # 5. Loop de conexao
    connected = False
    for i in range(5):
        print(f"\nTentativa de conexao {i+1}/5...")
        
        # Desconecta qualquer dispositivo atual
        run_command("bluetoothctl disconnect")
        time.sleep(0.5)
        
        # Tenta conectar
        connect_result = run_command(f"bluetoothctl connect {mac}")
        print(f"Resultado: {connect_result[:100]}...")
        
        # Aguarda estabilizar
        time.sleep(3)
        
        # Verifica conexao
        info_out = run_command(f"bluetoothctl info {mac}")
        if "Connected: yes" in info_out:
            connected = True
            print("Conexao bluetooth estabelecida!")
            break
        
        time.sleep(2)
    
    if not connected:
        print("FALHA: Nao foi possivel conectar via Bluetooth")
        run_command(f"bluetoothctl remove {mac}")
        return jsonify({
            "status": "error",
            "message": "Falha na conexão Bluetooth. Verifique se o dispositivo está em modo de pareamento."
        })
    
    # 6. Aguarda PulseAudio detectar o dispositivo
    print("\nBluetooth conectado! Aguardando PulseAudio...")
    time.sleep(3)
    
    # 7. Recarrega modulos para forcar deteccao
    reload_bluetooth_modules()
    
    # 8. Aguarda o sink de audio aparecer
    print("Aguardando audio bluetooth ficar disponivel...")
    sink_name = wait_for_bluetooth_audio(mac, timeout=25)
    
    if sink_name:
        print(f"\nSink encontrado: {sink_name}")
        
        # Configura o audio
        configure_audio_sink(sink_name)
        
        # Reinicia Raspotify para usar nova saida
        print("Reiniciando Raspotify...")
        time.sleep(1)
        subprocess.Popen(["sudo", "systemctl", "restart", "raspotify"])
        
        return jsonify({
            "status": "success",
            "message": f"Conectado e configurado! Sink: {sink_name}"
        })
    else:
        # Conectou bluetooth mas audio nao apareceu
        print("AVISO: Bluetooth conectado mas sink de audio nao apareceu")
        
        # Debug info
        cards = run_command("pactl list cards short")
        sinks = run_command("pactl list sinks short")
        print(f"Cards disponiveis:\n{cards}")
        print(f"Sinks disponiveis:\n{sinks}")
        
        return jsonify({
            "status": "warning",
            "message": "Bluetooth conectado, mas o áudio não configurou. Tente desconectar e conectar novamente."
        })


@app.route('/disconnect')
def disconnect():
    """Desconecta o dispositivo bluetooth atual"""
    print("Desconectando bluetooth...")
    run_command("bluetoothctl disconnect")
    
    # Volta para o P2 ou HAT
    time.sleep(2)
    sinks = run_command("pactl list sinks short")
    
    if PRIORITY_2_P2 in sinks.lower():
        fallback = get_sink_name(PRIORITY_2_P2, sinks)
    elif PRIORITY_3_HAT in sinks.lower():
        fallback = get_sink_name(PRIORITY_3_HAT, sinks)
    else:
        fallback = None
    
    if fallback:
        configure_audio_sink(fallback)
        print(f"Audio redirecionado para: {fallback}")
    
    return jsonify({"status": "success", "message": "Desconectado"})


@app.route('/remove/<mac>')
def remove(mac):
    """Remove um dispositivo pareado"""
    run_command(f"bluetoothctl remove {mac}")
    return jsonify({"status": "success", "message": f"Dispositivo {mac} removido"})


@app.route('/debug')
def debug():
    """Retorna informacoes de debug do sistema de audio"""
    return jsonify({
        "bluetooth_info": run_command("bluetoothctl info"),
        "paired_devices": run_command("bluetoothctl paired-devices"),
        "pulseaudio_cards": run_command("pactl list cards short"),
        "pulseaudio_sinks": run_command("pactl list sinks short"),
        "pulseaudio_modules": run_command("pactl list modules short | grep -i bluetooth"),
        "default_sink": run_command("pactl get-default-sink"),
        "sink_inputs": run_command("pactl list sink-inputs short")
    })


@app.route('/restart-audio')
def restart_audio():
    """Reinicia o sistema de audio (PulseAudio)"""
    print("Reiniciando PulseAudio...")
    run_command("pulseaudio -k")
    time.sleep(2)
    run_command("pulseaudio --start")
    time.sleep(2)
    ensure_bluetooth_modules()
    return jsonify({"status": "success", "message": "PulseAudio reiniciado"})


if __name__ == '__main__':
    print("\n" + "="*50)
    print("Bluetooth Audio Manager")
    print("="*50 + "\n")
    
    # Garante modulos na inicializacao
    ensure_bluetooth_modules()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
"""Teste do leitor de smartcard (hat SEC1210) na placa.

    mpremote cp ports/esp32p4/test_uscard.py :/test_uscard.py
    mpremote exec "exec(open('/test_uscard.py').read())"

Com um cartao no hat. Mostra em que etapa a conversa para: leitor, cartao, ATR
ou troca de APDU. O SELECT sem AID escolhe o aplicativo padrao do cartao, que
todo JavaCard responde, entao serve para qualquer cartao.
"""

import binascii
import time

import uscard


def run():
    connection = uscard.Reader(name="SEC1210 test").createConnection()

    started = time.ticks_ms()
    try:
        present = connection._reader.card_present()
    except uscard.SmartcardException as error:
        uart_id, tx, rx = uscard.active_pins()
        print("READER: no answer on UART%d (tx=%d rx=%d): %s"
              % (uart_id, tx, rx, error))
        print("        new CryptoGuide hat: UART2, tx=GPIO21, rx=GPIO28")
        print("        old Raspberry Pi hat: UART1, tx=GPIO21, rx=GPIO22 "
              "(after lifting R44/R45)")
        print("        check the 5 V supply and whether TX and RX are swapped")
        return False
    print("READER: answered in %d ms" % time.ticks_diff(time.ticks_ms(), started))

    if not present:
        print("CARD:   none in the slot")
        return False
    print("CARD:   present")

    try:
        connection.connect(connection.T1_protocol)
    except uscard.SmartcardException as error:
        print("ATR:    failed: %s" % error)
        return False
    print("ATR:    %s" % binascii.hexlify(connection.getATR()).decode())
    print("        protocol T=%d, IFSC %d" % (connection._protocol, connection._ifsc))

    try:
        started = time.ticks_ms()
        response = connection.transmit(b"\x00\xA4\x04\x00\x00")
    except uscard.SmartcardException as error:
        print("APDU:   failed: %s" % error)
        connection.disconnect()
        return False
    print("APDU:   SELECT -> %s (%d ms)"
          % (binascii.hexlify(response).decode(), time.ticks_diff(time.ticks_ms(), started)))
    connection.disconnect()
    return True


run()

# Waveshare ESP32-P4-WIFI6-Touch-LCD-4.3-C
#
# Duas familias de silicio: v0.x/v1.x (PRE_REV3) e v3.x. sdkconfig.p4 define
# ESP32P4_REV_MIN_300; sdkconfig.p4_pre_rev3 o substitui por REV_MIN_0. O build
# pre-rev3 inclui o fragmento PRE_REV3; o build v3.x passa -DP4_REV3=1 e o omite.
#
# Sem variante de WiFi de proposito: a placa carrega um ESP32-C6, mas o alvo e
# um dispositivo airgapped e o radio fica fora do build.

set(IDF_TARGET esp32p4)

# O sdkconfig nao aceita caminho relativo ao board, entao o da tabela de
# particoes e gerado aqui, absoluto, como mais um fragmento de defaults.
set(_P4_PARTITIONS_SDKCONFIG ${CMAKE_BINARY_DIR}/sdkconfig.partitions)
file(WRITE ${_P4_PARTITIONS_SDKCONFIG}
    "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=\"${CMAKE_CURRENT_LIST_DIR}/partitions.csv\"\n")

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.p4
    ${CMAKE_CURRENT_LIST_DIR}/sdkconfig.board
    ${_P4_PARTITIONS_SDKCONFIG}
)
# v0.x/v1.x: override REV_MIN_300 with REV_MIN_0. v3.x (-DP4_REV3=1): leave
# sdkconfig.p4's REV_MIN_300 in place.
if(NOT P4_REV3)
    list(INSERT SDKCONFIG_DEFAULTS 2 boards/sdkconfig.p4_pre_rev3)
endif()

set(MICROPY_FROZEN_MANIFEST ${CMAKE_CURRENT_LIST_DIR}/manifest.py)

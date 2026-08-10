# toolchain
CC           = sdcc
CP           = sdobjcopy
AS           = sdas8051
LD			= sdld
HEX          = packihx
BIN          = $(CP) -O binary -S

ifeq ($(OS),Windows_NT)
PYTHON ?= D:/Python310/python.exe
PS     ?= powershell -NoProfile -ExecutionPolicy Bypass -Command
else
PYTHON ?= python3
endif

# define mcu, specify the target processor
F_CPU   ?= 16000000
MCU          = mcs51
ARCH = mcs51

# ------------------------------------------------------
# Usually SDCC's small memory model is the best choice.  If
# you run out of internal RAM, you will need to declare
# variables as "xdata", or switch to largeer model

# Memory Model (small, medium, large, huge)
MODEL  = large
# ------------------------------------------------------
# Memory Layout
# PRG Size = 64K Bytes
CODE_SIZE = --code-loc 0x0000 --code-size 65536
# INT-MEM Size = 256 Bytes
IRAM_SIZE = --idata-loc 0x0000  --iram-size 256
# EXT-MEM Size = 3K Bytes
XRAM_SIZE = --xram-loc 0x0000 --xram-size 3072

# all the files will be generated with this name (main.elf, main.bin, main.hex, etc)
PROJECT_NAME=music-box-8051

# Storage backend: runtime-selectable via function pointer dispatcher
# Both backends are always compiled; backend chosen by storage_auto_detect() at boot

# specify define
DEFS       = STC8

# define root dir
ROOT_DIR     = .

# define include dir
INCLUDE_DIRS = TrackerPlayer WavetableSynth

# define lib dir
LIBDIR   = 

# user specific

SRC 	+= main.c
SRC 	+= WavetableSynth/WavetableSynth.c
SRC 	+= WavetableSynth/PitchTable.c
SRC 	+= TrackerPlayer/TrackerPlayer.c
SRC 	+= UartRedirect.c
SRC 	+= Bsp.c
SRC 	+= Protocol.c

SRC 	+= Storage.c
SRC 	+= SpiFlash.c
SRC 	+= scoreList.c

ASM_SRC =
ASM_SRC   += WavetableSynth/WavetableSynthAsm.s
ASM_SRC   += WavetableSynth/WavetableSynthStep.s
ASM_SRC   += WavetableSynth/PeriodTimer.s

INC_DIR  = $(patsubst %, -I%, $(INCLUDE_DIRS))
AS_INC   = $(INC_DIR)

# run from Flash
DDEFS	 = $(patsubst %, -D%, $(DEFS))
DEPS  = $(SRC:.c=.d)
OBJECTS  = $(SRC:.c=.rel) $(ASM_SRC:.s=.rel)
OTHER_OUTPUTS += $(ASM_SRC:.s=.asm) $(SRC:.c=.asm)
OTHER_OUTPUTS += $(ASM_SRC:.s=.lst) $(SRC:.c=.lst)
OTHER_OUTPUTS += $(ASM_SRC:.s=.rst) $(SRC:.c=.rst)
OTHER_OUTPUTS += $(ASM_SRC:.s=.sym) $(SRC:.c=.sym)
CFLAGS  = -m$(ARCH) -p$(MCU) --model-$(MODEL) --std-sdcc11
CFLAGS += -DF_CPU=$(F_CPU)UL -I. $(patsubst %, -I%, $(LIBDIR)) $(DDEFS) --stack-auto
ASFLAGS  = $(AS_INC) -plosgff -l -s
LD_FLAGS = -m$(ARCH) -l$(ARCH) --out-fmt-ihx -m$(MCU_MODEL) --model-$(MODEL) $(CODE_SIZE) $(IRAM_SIZE) $(XRAM_SIZE) --stack-auto

#
# makefile rules
#
all: $(OBJECTS) $(PROJECT_NAME).ihx $(PROJECT_NAME).hex $(PROJECT_NAME).bin

# Don't delete dependency files
.PRECIOUS: %.d

.PHONY: FORCE host-test compile-tracker generate-builtin-score

TRACKER_INPUT ?=
TRACKER_OUTPUT ?= output.t10p

host-test:
	@$(PYTHON) -m pytest -q tests/tracker10

compile-tracker:
	@$(PYTHON) tools/tracker10_tool.py compile "$(TRACKER_INPUT)" "$(TRACKER_OUTPUT)"

generate-builtin-score:
	@$(PYTHON) tools/gen_builtin_demo.py

# Don't rebuild deps if cleaning
ifneq ($(MAKECMDGOALS),clean)
-include $(DEPS)
# Beacuse SDCC's assembler has no way to auto output dependency info,
# the dependency is manually written here.	
WavetableSynth/PeriodTimer.rel: WavetableSynth/WavetableSynth.inc WavetableSynth/8051.inc WavetableSynth/UpdateTick.inc
WavetableSynth/WavetableSynthAsm.rel: WavetableSynth/WavetableSynth.inc
WavetableSynth/WavetableSynthStep.rel: WavetableSynth/WavetableSynth.inc
endif




ifeq ($(OS),Windows_NT)
%.rel: %.c Makefile
	@echo [CC] $(notdir $<)
# Output dependency
	@$(CC) $(CFLAGS) $(INC_DIR) -MM -c $< | $(PS) "$$input | ForEach-Object { $$_ -replace '^[^:]*:', '$@:' } | Set-Content -Encoding ASCII '$(patsubst %.c,%.d,$<)'"
# Do compiling
	@$(CC) $(CFLAGS) $(INC_DIR) -c $< -o $@

else
%.rel: %.c Makefile
	@echo [CC] $(notdir $<)
# Output dependency
	@$(CC) $(CFLAGS) $(INC_DIR) -MM -c $< | sed 's|^[^:]*:|$@:|' > $(patsubst %.c,%.d,$<)
# Do compiling
	@$(CC) $(CFLAGS) $(INC_DIR) -c $< -o $@

endif


%.rel: %.s
	@echo [AS] $(notdir $<)
	@$(AS) $(ASFLAGS) $<

%.ihx: $(OBJECTS)
	@echo [LD] $(PROJECT_NAME).ihx
	@$(CC) $(LD_FLAGS) $(OBJECTS) -o $@
	
%.hex: %.ihx
	@echo [HEX] $(PROJECT_NAME).hex
	@$(HEX) $< > $@	
	
%.bin: %.ihx
	@echo [BIN] $(PROJECT_NAME).bin
	@$(CP) -I ihex -O binary $< $@

# stcgal settings (open-source STC MCU ISP tool for Linux)
# install: pip3 install stcgal
# https://github.com/grigorig/stcgal
STCGAL_PORT   ?= /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
STCGAL_BAUD   ?= 115200
STCGAL_PROTO  ?= stc8d
STCGAL_BOOT_CMD ?= auto

BOOT_SCRIPT = $(PYTHON) tools/boot.py $(STCGAL_PORT) $(STCGAL_BAUD)

boot:
	@echo [BOOT] sending RESET frame to $(STCGAL_PORT)
	$(BOOT_SCRIPT)

flash: $(PROJECT_NAME).ihx
	@echo [BOOT] sending RESET frame to $(STCGAL_PORT)
	@$(BOOT_SCRIPT)
	@echo [FLASH] $(PROJECT_NAME).ihx via stcgal
	stcgal -P $(STCGAL_PROTO) -p $(STCGAL_PORT) -b $(STCGAL_BAUD) $<
	
ifeq ($(OS),Windows_NT)
clean:
	@echo [RM] OBJ
	@$(PS) "Remove-Item -Force -Recurse -ErrorAction SilentlyContinue $(foreach f,$(OBJECTS),'$(f)')"
	@echo [RM] HEX
	@$(PS) "Remove-Item -Force -Recurse -ErrorAction SilentlyContinue '$(PROJECT_NAME).ihx'"
	@echo [RM] Intermediate outputs
	@$(PS) "Remove-Item -Force -Recurse -ErrorAction SilentlyContinue $(foreach f,$(OTHER_OUTPUTS),'$(f)')"
	@$(PS) "Remove-Item -Force -Recurse -ErrorAction SilentlyContinue '$(PROJECT_NAME).lk' '$(PROJECT_NAME).map' '$(PROJECT_NAME).cdb' '$(PROJECT_NAME).hex' '$(PROJECT_NAME).bin' '$(PROJECT_NAME).mem'"
	@$(PS) "Remove-Item -Force -Recurse -ErrorAction SilentlyContinue $(foreach f,$(DEPS),'$(f)')"
else
clean:
	@echo [RM] OBJ
	@-rm -rf $(OBJECTS)
	@echo [RM] HEX
	@-rm -rf $(PROJECT_NAME).ihx
	@echo [RM] Intermediate outputs
	@-rm -rf $(OTHER_OUTPUTS)
	@-rm -rf $(PROJECT_NAME).lk
	@-rm -rf $(PROJECT_NAME).map	
	@-rm -rf $(PROJECT_NAME).cdb	
	@-rm -rf $(PROJECT_NAME).hex
	@-rm -rf $(PROJECT_NAME).bin
	@-rm -rf $(PROJECT_NAME).mem
	@-rm -rf $(DEPS)
endif

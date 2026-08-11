#include "Protocol.h"
#include "RegisterDefine.h"
#include "TrackerPlayer.h"
#include "WavetableSynth.h"
#include "Bsp.h"
#include "Storage.h"
#include "SpiFlash.h"

#define RX_BUF_SIZE 128
#define RX_MASK 127
#define PKT_BUF_SIZE 128
#define TX_BUF_SIZE 128

static MEM_XDATA(uint8_t) rx_ring[RX_BUF_SIZE];
static volatile uint8_t rx_wr, rx_rd;
static MEM_XDATA(uint8_t) pkt_data[PKT_BUF_SIZE];
static uint8_t pkt_len, pkt_pos, pkt_state, pkt_csum, pkt_cmd;
static MEM_XDATA(uint8_t) tx_buf[TX_BUF_SIZE];
static volatile uint8_t tx_len, tx_pos, tx_state;

static void wait_tx_idle(void)
{
    uint32_t started=GetSysMs();
    while(tx_state!=TX_IDLE) {
        if((uint32_t)(GetSysMs()-started)>20UL) {
            ES=0;
            tx_state=TX_IDLE;
            ES=1;
            break;
        }
    }
}

static uint8_t checksum(uint8_t start, uint8_t count)
{
    uint8_t i, c=0;
    for (i=0;i<count;i++) c^=tx_buf[start+i];
    return c;
}

static void start_tx(void)
{
    ES=0; tx_pos=1; tx_state=TX_SENDING; SBUF=tx_buf[0]; ES=1;
}

static void respond(uint8_t cmd,uint8_t status,const uint8_t *data,uint8_t len)
{
    uint8_t i;
    wait_tx_idle();
    tx_buf[0]=PROTO_SYNC; tx_buf[1]=cmd|PROTO_RSP_FLAG; tx_buf[2]=status; tx_buf[3]=len;
    for(i=0;i<len;i++) tx_buf[4+i]=data[i];
    tx_buf[4+len]=checksum(1,3+len); tx_len=5+len; start_tx();
}

static void ok(uint8_t cmd) { respond(cmd,STATUS_OK,0,0); }
static void error(uint8_t cmd,uint8_t status) { respond(cmd,status,0,0); }

static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}

static void flash_info(void)
{
    uint8_t data[9];
    SpiFlash_ReadJedecId(data);
    data[3]=(uint8_t)(SPI_FLASH_SIZE>>24); data[4]=(uint8_t)(SPI_FLASH_SIZE>>16);
    data[5]=(uint8_t)(SPI_FLASH_SIZE>>8); data[6]=(uint8_t)SPI_FLASH_SIZE;
    data[7]=(uint8_t)(SPI_FLASH_SECTOR_SIZE>>8); data[8]=(uint8_t)SPI_FLASH_SECTOR_SIZE;
    respond(CMD_FLASH_INFO,STATUS_OK,data,9);
}

static void flash_read(void)
{
    uint8_t data[120];
    uint8_t i, length;
    uint32_t address;
    if(pkt_len!=6) { error(CMD_FLASH_READ,STATUS_BAD_LEN); return; }
    address=read_u32(pkt_data); length=pkt_data[4];
    if(pkt_data[5] || length>sizeof(data) || address>SPI_FLASH_SIZE || length>SPI_FLASH_SIZE-address) {
        error(CMD_FLASH_READ,STATUS_INVALID_ADDR); return;
    }
    for(i=0;i<length;i++) data[i]=SpiFlash_ReadByte(address+i);
    respond(CMD_FLASH_READ,STATUS_OK,data,length);
}

static void dispatch(void)
{
    uint8_t data[84];
    uint8_t i,j;
    uint32_t now;
    int16_t mix;
    PlatformIrqState irq;
    switch(pkt_cmd) {
    case CMD_PING: {
        const char *s="TRACKER10-8051 v0.3";
        for(i=0;s[i];i++) data[i]=(uint8_t)s[i];
        respond(pkt_cmd,STATUS_OK,data,i); break;
    }
    case CMD_GET_INFO:
        data[0]=FW_VERSION_MAJOR; data[1]=FW_VERSION_MINOR; data[2]=storage_get_backend();
        data[3]=(uint8_t)mainPlayer.scheduler.trackCount; respond(pkt_cmd,STATUS_OK,data,4); break;
    case CMD_RESET:
        ok(pkt_cmd); wait_tx_idle(); IAP_CONTR=0x60; while(1);
    case CMD_UPTIME:
        now=GetSysMs(); data[0]=now; data[1]=now>>8; data[2]=now>>16; data[3]=now>>24;
        respond(pkt_cmd,STATUS_OK,data,4); break;
    case CMD_MEM_INFO:
        data[0]=SP; data[1]=0xff-SP; data[2]=0; data[3]=0; respond(pkt_cmd,STATUS_OK,data,4); break;
    case CMD_AUDIO_INFO:
        Platform_IrqSave(irq); mix=wavetableSynth.mixOut; Platform_IrqRestore(irq);
        data[0]=mix; data[1]=mix>>8; data[2]=wavetableSynth.pwmSample;
        data[3]=trackerQueue.underruns; data[4]=trackerQueue.head; data[5]=trackerQueue.tail;
        data[6]=wavetableSynth.muteMask; data[7]=wavetableSynth.muteMask>>8;
        respond(pkt_cmd,STATUS_OK,data,8); break;
    case CMD_ADC_READ:
        if(pkt_len!=1) error(pkt_cmd,STATUS_BAD_LEN);
        else { uint16_t v=Get_ADCResult(pkt_data[0]); data[0]=v>>8; data[1]=v; respond(pkt_cmd,STATUS_OK,data,2); } break;
    case CMD_VOICE_DUMP:
        for(i=0,j=0;i<WT_VOICE_COUNT;i++) {
            data[j++]=wavetableSynth.voice[i].phase[0]; data[j++]=wavetableSynth.voice[i].phase[1]; data[j++]=wavetableSynth.voice[i].phase[2];
            data[j++]=wavetableSynth.voice[i].increment[0]; data[j++]=wavetableSynth.voice[i].increment[1]; data[j++]=wavetableSynth.voice[i].increment[2];
            data[j++]=wavetableSynth.voice[i].volume; data[j++]=wavetableSynth.voice[i].waveOffset>>4;
        }
        respond(pkt_cmd,STATUS_OK,data,j); break;
    case CMD_PANIC: case CMD_STOP: TrackerPlayerStop(&mainPlayer); ok(pkt_cmd); break;
    case CMD_PLAY: TrackerPlayerPlay(&mainPlayer); ok(pkt_cmd); break;
    case CMD_PREV: TrackerPlayerPrevious(&mainPlayer); ok(pkt_cmd); break;
    case CMD_NEXT: TrackerPlayerNext(&mainPlayer); ok(pkt_cmd); break;
    case CMD_SET_SONG:
        if(pkt_len!=1) error(pkt_cmd,STATUS_BAD_LEN); else { TrackerPlayerSelect(&mainPlayer,pkt_data[0]); ok(pkt_cmd); } break;
    case CMD_GET_STATUS:
        data[0]=(uint8_t)mainPlayer.scheduler.currentTrack; data[1]=(uint8_t)mainPlayer.scheduler.trackCount;
        data[2]=mainPlayer.vm.status; data[3]=trackerLastError;
        data[4]=(uint8_t)mainPlayer.vm.order; data[5]=(uint8_t)(mainPlayer.vm.order>>8);
        data[6]=(uint8_t)mainPlayer.vm.row; data[7]=(uint8_t)(mainPlayer.vm.row>>8);
        data[8]=mainPlayer.vm.tick; data[9]=mainPlayer.vm.speed;
        respond(pkt_cmd,STATUS_OK,data,10); break;
    case CMD_FORMAT_INFO:
        data[0]='T';data[1]='1';data[2]='0';data[3]='M';data[4]=3;data[5]=WT_VOICE_COUNT;data[6]=0x00;data[7]=0x7d;
        respond(pkt_cmd,STATUS_OK,data,8); break;
    case CMD_CHANNEL_MUTE:
        if(pkt_len!=2 || (pkt_data[1]&0xfc)) error(pkt_cmd,STATUS_INVALID_PARAM);
        else { WavetableSynthSetMuteMask((uint16_t)pkt_data[0]|((uint16_t)pkt_data[1]<<8)); ok(pkt_cmd); } break;
    case CMD_SYS_INFO:
    case CMD_FLASH_INFO:
        if(storage_get_backend()!=STORAGE_BACKEND_SPI) error(pkt_cmd,STATUS_NOT_SUPPORTED); else flash_info(); break;
    case CMD_FLASH_READ_ID:
        if(storage_get_backend()!=STORAGE_BACKEND_SPI) error(pkt_cmd,STATUS_NOT_SUPPORTED);
        else { SpiFlash_ReadJedecId(data); respond(pkt_cmd,STATUS_OK,data,3); } break;
    case CMD_FLASH_READ:
        if(storage_get_backend()!=STORAGE_BACKEND_SPI) error(pkt_cmd,STATUS_NOT_SUPPORTED); else flash_read(); break;
    case CMD_FLASH_ERASE:
        if(storage_get_backend()!=STORAGE_BACKEND_SPI) error(pkt_cmd,STATUS_NOT_SUPPORTED);
        else if(pkt_len!=4) error(pkt_cmd,STATUS_BAD_LEN);
        else if(mainPlayer.vm.status==TRACKER_PLAYING) error(pkt_cmd,STATUS_NOT_SUPPORTED);
        else respond(pkt_cmd,SpiFlash_SectorErase(read_u32(pkt_data))?STATUS_FLASH_ERR:STATUS_OK,0,0); break;
    case CMD_FLASH_ERASE_ALL:
        if(storage_get_backend()!=STORAGE_BACKEND_SPI) error(pkt_cmd,STATUS_NOT_SUPPORTED);
        else if(mainPlayer.vm.status==TRACKER_PLAYING) error(pkt_cmd,STATUS_NOT_SUPPORTED);
        else respond(pkt_cmd,SpiFlash_ChipErase()?STATUS_FLASH_ERR:STATUS_OK,0,0); break;
    case CMD_FLASH_WRITE:
        if(storage_get_backend()!=STORAGE_BACKEND_SPI) error(pkt_cmd,STATUS_NOT_SUPPORTED);
        else if(pkt_len<5) error(pkt_cmd,STATUS_BAD_LEN);
        else if(mainPlayer.vm.status==TRACKER_PLAYING) error(pkt_cmd,STATUS_NOT_SUPPORTED);
        else respond(pkt_cmd,SpiFlash_PageProgram(read_u32(pkt_data),pkt_data+4,pkt_len-4)?STATUS_FLASH_ERR:STATUS_OK,0,0); break;
    default: error(pkt_cmd,STATUS_UNKNOWN_CMD); break;
    }
}

static void reset_parser(void) { pkt_state=PSTATE_IDLE; pkt_pos=0; pkt_csum=0; }
static void process_byte(uint8_t b)
{
    switch(pkt_state) {
    case PSTATE_IDLE: if(b==PROTO_SYNC){pkt_state=PSTATE_CMD;pkt_csum=0;} break;
    case PSTATE_CMD: pkt_cmd=b;pkt_csum^=b;pkt_state=PSTATE_LEN;break;
    case PSTATE_LEN: pkt_len=b;pkt_csum^=b;pkt_pos=0;pkt_state=b?PSTATE_DATA:PSTATE_CSUM;break;
    case PSTATE_DATA: if(pkt_pos<PKT_BUF_SIZE)pkt_data[pkt_pos]=b;pkt_pos++;pkt_csum^=b;if(pkt_pos>=pkt_len)pkt_state=PSTATE_CSUM;break;
    case PSTATE_CSUM: if(b==pkt_csum)dispatch();reset_parser();break;
    }
}

void Proto_ISR_Rx(uint8_t b) { uint8_t n=(rx_wr+1)&RX_MASK;if(n!=rx_rd){rx_ring[rx_wr]=b;rx_wr=n;} }
void Proto_ISR_TxNextByte(void) { if(tx_state==TX_SENDING){if(tx_pos<tx_len)SBUF=tx_buf[tx_pos++];else tx_state=TX_IDLE;} }
void Proto_Init(void) { rx_wr=rx_rd=0;tx_state=TX_IDLE;reset_parser(); }
void Proto_Process(void) { while(rx_wr!=rx_rd){uint8_t b=rx_ring[rx_rd];rx_rd=(rx_rd+1)&RX_MASK;process_byte(b);} }

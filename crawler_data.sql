/*
 Navicat Premium Dump SQL

 Source Server         : zyxww
 Source Server Type    : MySQL
 Source Server Version : 80022 (8.0.22)
 Source Host           : 127.0.0.1:3306
 Source Schema         : crawler_data

 Target Server Type    : MySQL
 Target Server Version : 80022 (8.0.22)
 File Encoding         : 65001

 Date: 08/06/2026 09:56:09
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for crawler_news_0
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_0`;
CREATE TABLE `crawler_news_0`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2546 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_1
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_1`;
CREATE TABLE `crawler_news_1`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2552 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_2
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_2`;
CREATE TABLE `crawler_news_2`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2547 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_3
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_3`;
CREATE TABLE `crawler_news_3`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2549 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_4
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_4`;
CREATE TABLE `crawler_news_4`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2554 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_5
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_5`;
CREATE TABLE `crawler_news_5`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2554 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_6
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_6`;
CREATE TABLE `crawler_news_6`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2551 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_7
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_7`;
CREATE TABLE `crawler_news_7`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2549 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_8
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_8`;
CREATE TABLE `crawler_news_8`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2544 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_9
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_9`;
CREATE TABLE `crawler_news_9`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `news_id` int UNSIGNED NOT NULL COMMENT '关联 crawler_news_main 表 id',
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '新闻内容',
  `file_urls` json NULL COMMENT '内容中被引用的文件地址，JSON格式',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_news_id`(`news_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2551 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_news_main
-- ----------------------------
DROP TABLE IF EXISTS `crawler_news_main`;
CREATE TABLE `crawler_news_main`  (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键自增id',
  `belong_type` tinyint(1) NOT NULL DEFAULT 1 COMMENT '所属 1-网站采集 2-公众号采集',
  `repo_kind` tinyint(1) NOT NULL DEFAULT 1 COMMENT '类型 1院校 2中外合作库',
  `college_id` int NOT NULL COMMENT '院校id',
  `college_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '院校名称',
  `specialty_id` int NULL DEFAULT NULL COMMENT '专业id',
  `specialty_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '专业名称',
  `category` tinyint NULL DEFAULT NULL COMMENT '信息分类。（1-招生简章，2-信息发布，3-网站公告，5-调剂）',
  `title` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '标题',
  `author` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '作者',
  `keywords` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '关键字',
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '描述',
  `views` int NULL DEFAULT 0 COMMENT '访问量',
  `original_url` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '文章的原始内容来源路径',
  `weight` tinyint NULL DEFAULT NULL COMMENT '权重',
  `no_realviews` int NULL DEFAULT NULL COMMENT '（虚构）点击量',
  `image` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '封面图',
  `is_top` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否置顶',
  `state` tinyint(1) NULL DEFAULT NULL COMMENT '状态：1-显示，0-隐藏',
  `is_del` tinyint UNSIGNED NOT NULL DEFAULT 0 COMMENT '是否删除 0-否 1-是',
  `publish_date` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT '' COMMENT '发布时间',
  `score` int NULL DEFAULT 0 COMMENT '系统评分',
  `score_detail` json NULL COMMENT '评分详情',
  `ctime` int NOT NULL COMMENT '	创建时间',
  `utime` int UNSIGNED NOT NULL DEFAULT 0 COMMENT '最后修改时间',
  `settings_id` int NULL DEFAULT NULL COMMENT '是根据那个配置采集的数据',
  `information_id` int NULL DEFAULT NULL COMMENT '发布到文章表的ID',
  `is_research_institute` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否研究院 0否 1是',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_original_url`(`original_url` ASC) USING BTREE COMMENT '去重索引',
  INDEX `idx_tbl_college_information_college_id`(`college_id` ASC) USING BTREE,
  INDEX `idx_tbl_college_information_specialty_id`(`specialty_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 25606 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '记录院校的信息，包括招生、调剂，信息发布。这类信息，主要通过数据抓取的方式从院校网站、系网站抓取而来。' ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for crawler_settings
-- ----------------------------
DROP TABLE IF EXISTS `crawler_settings`;
CREATE TABLE `crawler_settings`  (
  `id` int UNSIGNED NOT NULL AUTO_INCREMENT,
  `title` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '爬虫规则的名称',
  `repo_kind` tinyint(1) NOT NULL DEFAULT 1 COMMENT '类型 1院校 2中外合作库',
  `college_id` int UNSIGNED NOT NULL COMMENT '院校ID',
  `college_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT '' COMMENT '院校名称',
  `specialty_id` int UNSIGNED NOT NULL DEFAULT 0 COMMENT '专业ID',
  `specialty_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT '' COMMENT '专业名称',
  `url` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT '' COMMENT '抓取网站地址',
  `domain` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT '',
  `rule` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '采集抓取规则',
  `status` tinyint UNSIGNED NOT NULL DEFAULT 0 COMMENT '状态 0-关闭 1-开启',
  `category` tinyint UNSIGNED NOT NULL DEFAULT 1 COMMENT '	信息分类。（1-招生简章，2-信息发布，3-网站公告，5-调剂）',
  `is_del` tinyint UNSIGNED NOT NULL DEFAULT 0 COMMENT '是否删除 0-否 1-是',
  `ctime` int NOT NULL COMMENT '创建时间',
  `utime` int NOT NULL COMMENT '最后修改时间',
  `is_research_institute` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否研究院 0否 1是',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 61 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '院校专业采集设置信息' ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for wechat_mp_config
-- ----------------------------
DROP TABLE IF EXISTS `wechat_mp_config`;
CREATE TABLE `wechat_mp_config`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT '标题',
  `college_id` int NOT NULL DEFAULT 0 COMMENT '所属院校ID',
  `specialty_id` int NULL DEFAULT NULL COMMENT '专业ID，院校类型时可选',
  `college_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL DEFAULT '' COMMENT '院校名称',
  `specialty_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL DEFAULT '' COMMENT '专业名称',
  `repo_kind` tinyint NOT NULL DEFAULT 1 COMMENT '1=院校 2=中外合作库',
  `category` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL DEFAULT '' COMMENT '信息分类编码',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '0=停用 1=启用',
  `mp_list` json NULL COMMENT '公众号列表',
  `ctime` int NOT NULL COMMENT '创建时间',
  `utime` int NOT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_status`(`status` ASC) USING BTREE,
  INDEX `idx_college`(`college_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 229 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '公众号采集配置' ROW_FORMAT = Dynamic;

-- ----------------------------
-- Procedure structure for CleanEmptyNews
-- ----------------------------
DROP PROCEDURE IF EXISTS `CleanEmptyNews`;
delimiter ;;
CREATE PROCEDURE `CleanEmptyNews`()
BEGIN
    DECLARE i INT DEFAULT 0;
    
    -- 循环 0 到 9
    WHILE i < 10 DO
        -- 拼接动态 SQL
        SET @delSql = CONCAT(
            'DELETE t_main, t_sub ',
            'FROM crawler_news_main t_main ',
            'INNER JOIN crawler_news_', i, ' t_sub ON t_main.id = t_sub.news_id ',
            'WHERE (t_sub.content IS NULL OR t_sub.content = "") ',
            -- 加上这一句，确保只操作符合分表规则的 ID
            'AND (t_main.id % 10 = ', i, ')' 
        );

        -- 预处理并执行
        PREPARE stmt FROM @delSql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
        
        SET i = i + 1;
    END WHILE;
END
;;
delimiter ;

SET FOREIGN_KEY_CHECKS = 1;
